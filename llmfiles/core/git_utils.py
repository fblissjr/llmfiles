# llmfiles/git_utils.py
"""
utility functions for interacting with git repositories using subprocess.
abstracts git command execution and error handling.
"""
import subprocess
from pathlib import Path
from typing import Optional, Tuple
import structlog  # for structured logging

from llmfiles.exceptions import GitError  # custom exception for git-related errors

log = structlog.get_logger(__name__)  # module-level logger


def _run_git_command(
    args: list[str], repo_path: Path, check_exit_code: bool = True
) -> Tuple[bool, str, str]:
    """
    runs a git command via subprocess.
    returns a tuple: (success_flag, stdout_str, stderr_str).
    if `check_exit_code` is true, raises `GitError` on non-zero exit.
    """
    try:
        command_parts = ["git"] + [
            str(arg) for arg in args
        ]  # ensure all args are strings
        log.debug(
            "executing_git_command", command=" ".join(command_parts), cwd=str(repo_path)
        )

        process = subprocess.run(
            command_parts,
            capture_output=True,  # capture stdout and stderr
            text=True,  # decode output as text (utf-8 by default)
            check=False,  # handle exit code manually based on `check_exit_code`
            cwd=repo_path,  # execute in the context of the repo path
            encoding="utf-8",  # be explicit about encoding
            errors="replace",  # handle potential decoding errors in git's output
        )

        was_successful = process.returncode == 0
        stdout_content = (process.stdout or "").strip()
        stderr_content = (process.stderr or "").strip()

        if not was_successful:
            error_details = {
                "command": " ".join(command_parts),
                "exit_code": process.returncode,
                "repo_path": str(repo_path),
                "stderr": stderr_content if stderr_content else "(empty)",
            }
            log.warning("git_command_failed", **error_details)
            if check_exit_code:
                raise GitError(f"git command failed. details: {error_details}")

        return was_successful, stdout_content, stderr_content

    except FileNotFoundError:  # 'git' executable not found
        log.error(
            "git_executable_not_found",
            note="ensure git is installed and in your system's path.",
        )
        raise GitError("git command not found. is git installed and in path?") from None
    except Exception as e:  # other unexpected errors during subprocess execution
        log.error(
            "git_command_unexpected_error",
            command_args=args,
            error=str(e),
            exc_info=True,
        )
        raise GitError(
            f"unexpected error running git command {' '.join(args)}: {e}"
        ) from e

def check_is_git_repo(path_to_check: Path) -> bool:
    """checks if the given path is inside a git working tree."""
    try:
        # `git rev-parse --is-inside-work-tree` reliably checks this.
        # it exits 0 if true, non-zero otherwise or if not a repo.
        # `check_exit_code=false` because non-zero is an expected outcome here.
        is_repo, _, _ = _run_git_command(
            ["rev-parse", "--is-inside-work-tree"], path_to_check, check_exit_code=False
        )
        return is_repo
    except GitError:  # if `_run_git_command` itself fails (e.g., git not found)
        log.debug(
            "git_error_during_repo_check",
            path=str(path_to_check),
            note="assuming not a git repository due to error.",
        )
        return False  # assume not a verifiable git repo if git command fails


def get_diff(repo_path: Path) -> Optional[str]:
    """
    gets the staged git diff (changes between head and the index).
    returns the diff output string, empty string if no diff, or none on error.
    """
    if not check_is_git_repo(repo_path):
        log.info("not_a_git_repository_skip_staged_diff", path=str(repo_path))
        return ""  # treat as "no diff" to simplify calling code.

    try:
        # `--patch` for full diff, `--no-color` for clean, machine-readable output.
        success, stdout_str, stderr_str = _run_git_command(
            ["diff", "--staged", "--patch", "--no-color"],
            repo_path,
            check_exit_code=False,  # specific "errors" (like new repo) are handled gracefully.
        )
        if not success:
            # common case: new repository before first commit (head doesn't exist).
            if (
                "fatal: bad revision 'head'" in stderr_str.lower()
                or "fatal: empty repository" in stderr_str.lower()
            ):
                log.info(
                    "no_head_revision_or_empty_repo_assuming_no_staged_diff",
                    path=str(repo_path),
                )
                return ""  # no staged changes in this scenario.
            else:  # other errors during `git diff`.
                log.error(
                    "failed_to_get_staged_git_diff",
                    path=str(repo_path),
                    stderr=stderr_str,
                )
                return None  # indicate an actual error occurred.
        return (
            stdout_str  # empty string if no staged changes, otherwise the diff content.
        )
    except GitError as e:  # errors from `_run_git_command` itself.
        log.error("giterror_getting_staged_diff", path=str(repo_path), error=str(e))
        return None


def get_diff_branches(repo_path: Path, branch1: str, branch2: str) -> Optional[str]:
    """
    gets the git diff between two specified branches.
    returns diff string, empty string if no diff, or none on error.
    """
    if not check_is_git_repo(repo_path):
        log.info("not_a_git_repository_skip_branch_diff", path=str(repo_path))
        return ""

    try:
        # `branch1...branch2` (three dots) shows changes on branch2 since common ancestor with branch1.
        # use `--no-color` for clean output.
        diff_range = f"{branch1}...{branch2}"
        success, stdout_str, stderr_str = _run_git_command(
            ["diff", "--patch", "--no-color", diff_range],
            repo_path,
            check_exit_code=False,  # command might fail if branches don't exist.
        )
        if not success:
            log.error(
                "failed_to_get_branch_diff",
                path=str(repo_path),
                branch1=branch1,
                branch2=branch2,
                stderr=stderr_str,
            )
            return None
        return stdout_str
    except GitError as e:
        log.error(
            "giterror_getting_branch_diff",
            path=str(repo_path),
            branch1=branch1,
            branch2=branch2,
            error=str(e),
        )
        return None


def get_log_branches(repo_path: Path, branch1: str, branch2: str) -> Optional[str]:
    """
    gets git log of commits in `branch2` but not in `branch1`.
    returns formatted log string, empty string if no such commits, or none on error.
    """
    if not check_is_git_repo(repo_path):
        log.info("not_a_git_repository_skip_branch_log", path=str(repo_path))
        return ""

    # format: short hash, author date (short yyyy-mm-dd), author name, subject.
    log_format = "--pretty=format:%h - %ad - %an: %s"
    date_format = "--date=short"
    commit_range = f"{branch1}..{branch2}"  # commits in branch2 not in branch1.

    try:
        success, stdout_str, stderr_str = _run_git_command(
            ["log", log_format, date_format, "--no-color", commit_range],
            repo_path,
            check_exit_code=False,  # range might be invalid or yield no commits.
        )
        if not success:
            log.error(
                "failed_to_get_branch_log",
                path=str(repo_path),
                branch1=branch1,
                branch2=branch2,
                stderr=stderr_str,
            )
            return None
        return stdout_str
    except GitError as e:
        log.error(
            "giterror_getting_branch_log",
            path=str(repo_path),
            branch1=branch1,
            branch2=branch2,
            error=str(e),
        )
        return None