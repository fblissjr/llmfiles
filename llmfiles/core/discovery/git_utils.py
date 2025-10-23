# llmfiles/core/discovery/git_utils.py
import subprocess
from pathlib import Path
from typing import Set, Optional
import structlog

log = structlog.get_logger(__name__)


def get_git_modified_files(since: str, base_dir: Path) -> Optional[Set[Path]]:
    """
    Get a set of files that have been modified in git since the specified date.

    Args:
        since: Git date specification (e.g., "7 days ago", "2025-01-01", "1 week ago")
        base_dir: The base directory to run git commands from

    Returns:
        Set of Path objects for modified files, or None if git command fails
    """
    try:
        # Check if we're in a git repository
        check_cmd = ["git", "rev-parse", "--git-dir"]
        result = subprocess.run(
            check_cmd,
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            log.warning("not_a_git_repository", base_dir=str(base_dir))
            return None

        # Get files modified since the specified date
        # Using git log to get all files that have been touched
        git_cmd = [
            "git", "log",
            f"--since={since}",
            "--name-only",
            "--pretty=format:",
            "--diff-filter=d"  # Exclude deleted files
        ]

        result = subprocess.run(
            git_cmd,
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            log.error(
                "git_command_failed",
                command=" ".join(git_cmd),
                error=result.stderr,
                base_dir=str(base_dir)
            )
            return None

        # Parse output and convert to absolute paths
        modified_files = set()
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line:  # Skip empty lines
                file_path = (base_dir / line).resolve()
                if file_path.exists():  # Only include files that still exist
                    modified_files.add(file_path)

        log.info(
            "git_modified_files_found",
            count=len(modified_files),
            since=since,
            base_dir=str(base_dir)
        )

        return modified_files

    except Exception as e:
        log.error(
            "git_utils_error",
            error=str(e),
            since=since,
            base_dir=str(base_dir),
            exc_info=True
        )
        return None


def is_git_repository(base_dir: Path) -> bool:
    """
    Check if the given directory is within a git repository.

    Args:
        base_dir: The directory to check

    Returns:
        True if in a git repository, False otherwise
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=base_dir,
            capture_output=True,
            check=False
        )
        return result.returncode == 0
    except Exception:
        return False
