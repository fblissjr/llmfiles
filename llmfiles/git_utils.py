# llmfiles/git_utils.py
"""Git operations using subprocess."""
import subprocess
import logging
from pathlib import Path
from typing import Optional, Tuple

from .exceptions import GitError

logger = logging.getLogger(__name__)

def _run_git_command(args: list[str], repo_path: Path) -> Tuple[bool, str, str]:
    """Runs a git command and returns success status, stdout, stderr."""
    try:
        logger.debug(f"Running git command: {' '.join(['git'] + args)} in {repo_path}")
        process = subprocess.run(
            ['git'] + args,
            capture_output=True,
            text=True,
            check=False, # Don't raise exception on non-zero exit
            cwd=repo_path,
            encoding='utf-8'
        )
        success = process.returncode == 0
        if not success:
            logger.warning(f"Git command failed (exit code {process.returncode}): git {' '.join(args)}")
            logger.warning(f"Git stderr: {process.stderr.strip()}")
        return success, process.stdout, process.stderr
    except FileNotFoundError:
        raise GitError("Git command not found. Is Git installed and in your PATH?")
    except Exception as e:
        raise GitError(f"Failed to run git command {' '.join(args)}: {e}")

def check_is_git_repo(repo_path: Path) -> bool:
     """Checks if a path is within a Git repository."""
     # Use rev-parse --is-inside-work-tree which is reliable
     success, _, _ = _run_git_command(['rev-parse', '--is-inside-work-tree'], repo_path)
     return success


def get_diff(repo_path: Path) -> Optional[str]:
    """Gets staged git diff (HEAD vs index)."""
    if not check_is_git_repo(repo_path):
        logger.warning(f"Path is not a git repository: {repo_path}")
        return None

    success, stdout, stderr = _run_git_command(['diff', '--staged', '--patch'], repo_path)
    if not success:
        # Diff command can fail if HEAD doesn't exist (new repo) - this is okay
        if "unknown revision or path not in the working tree" in stderr:
             logger.info("No HEAD revision found, assuming no staged diff.")
             return "" # No diff in this case
        else:
            logger.error(f"Failed to get git diff: {stderr.strip()}")
            return None # Indicate error
    return stdout if stdout.strip() else "" # Return empty string if no diff, None on error

def get_diff_branches(repo_path: Path, branch1: str, branch2: str) -> Optional[str]:
    """Gets git diff between two branches."""
    if not check_is_git_repo(repo_path):
        logger.warning(f"Path is not a git repository: {repo_path}")
        return None

    # Check if branches exist? - Can be complex with remotes. Git diff will fail anyway.
    success, stdout, stderr = _run_git_command(['diff', '--patch', f"{branch1}..{branch2}"], repo_path)
    if not success:
        logger.error(f"Failed to get git diff between {branch1} and {branch2}: {stderr.strip()}")
        return None
    return stdout if stdout.strip() else ""

def get_log_branches(repo_path: Path, branch1: str, branch2: str) -> Optional[str]:
    """Gets git log between two branches."""
    if not check_is_git_repo(repo_path):
        logger.warning(f"Path is not a git repository: {repo_path}")
        return None

    # Use a simple format, can be customized if needed
    log_format = "--pretty=format:%h - %s"
    success, stdout, stderr = _run_git_command(['log', log_format, f"{branch1}..{branch2}"], repo_path)
    if not success:
        logger.error(f"Failed to get git log between {branch1} and {branch2}: {stderr.strip()}")
        return None
    return stdout.strip() if stdout.strip() else ""