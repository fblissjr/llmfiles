# llmfiles/git_utils.py
"""
Utility functions for interacting with Git repositories using subprocess.
This module abstracts away the direct calls to the 'git' command-line tool.
"""

import subprocess
import logging
from pathlib import Path
from typing import Optional, Tuple

from .exceptions import GitError  # Custom exception for Git-related errors

logger = logging.getLogger(__name__)


def _run_git_command(
    args: list[str],
    repo_path: Path,
    check_exit_code: bool = True,  # If True, raise GitError on non-zero exit.
) -> Tuple[bool, str, str]:
    """
    Runs a Git command using subprocess and returns its success status, stdout, and stderr.

    Args:
        args: A list of string arguments for the 'git' command (e.g., ['diff', '--staged']).
        repo_path: The Path object representing the directory of the Git repository
                   (or a subdirectory within it) where the command should be run.
        check_exit_code: If True, a GitError is raised if the command returns a non-zero exit code.
                         If False, the command's success status is returned along with outputs.

    Returns:
        A tuple (success: bool, stdout: str, stderr: str).
        'success' is True if the command exited with 0, False otherwise.

    Raises:
        GitError: If 'git' command is not found, if an unexpected error occurs during execution,
                  or if `check_exit_code` is True and the command fails.
    """
    try:
        # Ensure all arguments are strings for subprocess.
        str_args = [str(arg) for arg in args]
        command_to_run = ["git"] + str_args

        logger.debug(
            f'Executing Git command: "{" ".join(command_to_run)}" in directory: {repo_path}'
        )

        process = subprocess.run(
            command_to_run,
            capture_output=True,  # Capture stdout and stderr.
            text=True,  # Decode output as text (default UTF-8).
            check=False,  # Do not raise CalledProcessError automatically; we handle it.
            cwd=repo_path,  # Set current working directory for the command.
            encoding="utf-8",  # Be explicit about encoding for robustness.
            errors="replace",  # Handle potential decoding errors in Git's output.
        )

        success = process.returncode == 0
        stdout_str = process.stdout.strip() if process.stdout else ""
        stderr_str = process.stderr.strip() if process.stderr else ""

        if not success:
            error_message_detail = (
                f"Git command `git {' '.join(str_args)}` failed with exit code {process.returncode}.\n"
                f"Repository: {repo_path}\n"
                f"Stderr: {stderr_str if stderr_str else '(empty)'}"
            )
            logger.warning(error_message_detail)  # Log all failures
            if check_exit_code:
                # If strict checking is required, raise an error.
                raise GitError(error_message_detail)

        return success, stdout_str, stderr_str

    except FileNotFoundError:
        # This occurs if 'git' executable is not found in the system's PATH.
        msg = "Git command not found. Please ensure Git is installed and accessible in your system's PATH."
        logger.error(msg)
        raise GitError(
            msg
        ) from None  # `from None` suppresses the FileNotFoundError in traceback
    except Exception as e:
        # Catch any other unexpected exceptions during subprocess execution.
        msg = f"An unexpected error occurred while trying to run git command `git {' '.join(args)}`: {e}"
        logger.error(msg, exc_info=True)  # Log with traceback for debugging
        raise GitError(msg) from e


def check_is_git_repo(path_to_check: Path) -> bool:
    """
    Checks if the given path is inside a Git working tree.

    Args:
       path_to_check: The directory Path to check.

    Returns:
       True if the path is part of a Git repository, False otherwise (e.g., not a repo, or git command failed).
    """
    try:
        # `git rev-parse --is-inside-work-tree` is a reliable way to check.
        # It exits with 0 if inside a work tree, non-zero otherwise (or if not a repo).
        # `check_exit_code=False` as non-zero exit is expected if not a repo.
        success, _, _ = _run_git_command(
            ["rev-parse", "--is-inside-work-tree"], path_to_check, check_exit_code=False
        )
        return success
    except GitError:
        # This can happen if _run_git_command itself fails (e.g., git not found).
        # In such cases, we can assume it's not a verifiable git repo.
        logger.debug(
            f"GitError while checking if {path_to_check} is a repo; assuming false."
        )
        return False


def get_diff(repo_path: Path) -> Optional[str]:
    """
    Gets the staged Git diff (changes between HEAD and the index).

    Args:
        repo_path: Path to the Git repository.

    Returns:
        The diff output as a string. Returns an empty string if there are no staged changes
        or if the repository is new/empty. Returns None if an error occurred while fetching the diff.
    """
    if not check_is_git_repo(repo_path):
        logger.info(
            f"Path {repo_path} is not a git repository or git is unavailable. Cannot get staged diff."
        )
        return ""  # Treat as "no diff" if not a repo to simplify calling code.

    try:
        # `--patch` for full diff, `--no-color` for clean machine-readable output.
        success, stdout_str, stderr_str = _run_git_command(
            ["diff", "--staged", "--patch", "--no-color"],
            repo_path,
            check_exit_code=False,  # Handle specific "errors" like new repo gracefully.
        )

        if not success:
            # A common "failure" for `git diff --staged` is in a new repository before the first commit
            # (HEAD doesn't exist). This is not a true error for our purposes, just means no staged diff.
            if (
                "fatal: bad revision 'HEAD'" in stderr_str
                or "fatal: empty repository" in stderr_str
            ):
                logger.info(
                    f"No HEAD revision or empty repository at {repo_path}. Assuming no staged diff."
                )
                return ""  # No staged changes in this case.
            else:
                # For other errors, log them and indicate failure by returning None.
                logger.error(
                    f"Failed to get staged git diff for {repo_path}. Stderr: {stderr_str}"
                )
                return None  # Indicate an actual error occurred.

        return (
            stdout_str  # This will be an empty string if there are no staged changes.
        )
    except GitError as e:
        # Catch errors from _run_git_command itself (e.g., git not found).
        logger.error(
            f"GitError encountered while getting staged diff for {repo_path}: {e}"
        )
        return None


def get_diff_branches(repo_path: Path, branch1: str, branch2: str) -> Optional[str]:
    """
    Gets the Git diff between two specified branches.

    Args:
        repo_path: Path to the Git repository.
        branch1: The base branch name.
        branch2: The branch to compare against the base branch.

    Returns:
        The diff output as a string. Returns an empty string if there are no differences.
        Returns None if an error occurred (e.g., branches not found, git error).
    """
    if not check_is_git_repo(repo_path):
        logger.info(
            f"Path {repo_path} is not a git repository. Cannot get branch diff."
        )
        return ""

    try:
        # `branch1...branch2` (three dots) shows changes on branch2 since it forked from branch1 (symmetric diff).
        # `branch1..branch2` (two dots) shows changes on branch2 that are not on branch1.
        # Three dots is often what users mean by "diff between branches".
        diff_range = f"{branch1}...{branch2}"
        success, stdout_str, stderr_str = _run_git_command(
            ["diff", "--patch", "--no-color", diff_range],
            repo_path,
            check_exit_code=False,  # Branches might not exist, git diff will report this.
        )
        if not success:
            logger.error(
                f"Failed to get git diff between branches '{branch1}' and '{branch2}' for {repo_path}. "
                f"Stderr: {stderr_str}"
            )
            return None  # Indicate error.
        return stdout_str  # Empty string if no differences.
    except GitError as e:
        logger.error(
            f"GitError during branch diff for {repo_path} ({branch1}...{branch2}): {e}"
        )
        return None


def get_log_branches(repo_path: Path, branch1: str, branch2: str) -> Optional[str]:
    """
    Gets the Git log of commits that are in `branch2` but not in `branch1`.

    Args:
        repo_path: Path to the Git repository.
        branch1: The base branch name (commits up to this branch are excluded).
        branch2: The target branch name (commits from this branch are included).

    Returns:
        The formatted log output as a string. Returns an empty string if no such commits.
        Returns None if an error occurred.
    """
    if not check_is_git_repo(repo_path):
        logger.info(f"Path {repo_path} is not a git repository. Cannot get branch log.")
        return ""

    # Customizable log format: short hash, author date (short), author name, subject.
    log_format_str = "--pretty=format:%h - %ad - %an: %s"
    date_format_str = "--date=short"  # Format date as YYYY-MM-DD.
    commit_range = f"{branch1}..{branch2}"  # Commits in branch2 not in branch1.

    try:
        success, stdout_str, stderr_str = _run_git_command(
            ["log", log_format_str, date_format_str, "--no-color", commit_range],
            repo_path,
            check_exit_code=False,  # Range might be invalid or empty.
        )
        if not success:
            logger.error(
                f"Failed to get git log between branches '{branch1}' and '{branch2}' for {repo_path}. "
                f"Stderr: {stderr_str}"
            )
            return None
        return stdout_str  # Empty string if no relevant commits.
    except GitError as e:
        logger.error(
            f"GitError during branch log for {repo_path} ({branch1}..{branch2}): {e}"
        )
        return None