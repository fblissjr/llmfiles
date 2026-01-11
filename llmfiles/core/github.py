# llmfiles/core/github.py
"""GitHub repository handling for llmfiles.

Provides functionality to detect GitHub URLs and clone repositories
to temporary directories for processing.
"""

import re
import subprocess
from pathlib import Path

import structlog

from llmfiles.exceptions import GitError

log = structlog.get_logger(__name__)

# Matches common GitHub URL patterns:
# - https://github.com/user/repo
# - https://github.com/user/repo.git
# - github.com/user/repo (will be prefixed with https://)
GITHUB_URL_PATTERN = re.compile(
    r"^(?:https?://)?github\.com/[\w.\-]+/[\w.\-]+(?:\.git)?/?$",
    re.IGNORECASE,
)


def is_github_url(path_str: str) -> bool:
    """Check if string is a GitHub repository URL.

    Args:
        path_str: String to check (may be a local path or URL)

    Returns:
        True if the string matches a GitHub URL pattern
    """
    return bool(GITHUB_URL_PATTERN.match(path_str.strip()))


def normalize_github_url(url: str) -> str:
    """Normalize GitHub URL to ensure https:// prefix.

    Args:
        url: GitHub URL (may or may not have scheme)

    Returns:
        URL with https:// prefix
    """
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def clone_github_repo(url: str, target_dir: Path) -> Path:
    """Clone a GitHub repository to the target directory.

    Uses shallow clone (--depth=1) for faster cloning.

    Args:
        url: GitHub repository URL
        target_dir: Directory to clone into (repo will be cloned as 'repo' subdirectory)

    Returns:
        Path to the cloned repository

    Raises:
        GitError: If git clone fails or git is not installed
    """
    url = normalize_github_url(url)
    clone_path = target_dir / "repo"

    log.info("cloning_github_repo", url=url, target=str(clone_path))

    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", url, str(clone_path)],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            log.error("git_clone_failed", url=url, error=error_msg)
            raise GitError(f"git clone failed: {error_msg}")

        log.info("clone_successful", url=url, path=str(clone_path))
        return clone_path

    except FileNotFoundError:
        raise GitError("git command not found - please install git")
    except OSError as e:
        raise GitError(f"failed to run git command: {e}")
