# tests/test_github.py
"""Tests for GitHub URL detection and repository cloning."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

from llmfiles.core.github import is_github_url, normalize_github_url, clone_github_repo
from llmfiles.exceptions import GitError


class TestIsGithubUrl:
    """Tests for GitHub URL detection."""

    @pytest.mark.parametrize("url", [
        "https://github.com/user/repo",
        "https://github.com/user/repo.git",
        "https://github.com/user-name/repo-name",
        "https://github.com/user/repo/",
        "github.com/user/repo",
        "HTTPS://GITHUB.COM/user/repo",  # case insensitive
    ])
    def test_valid_github_urls(self, url):
        """Valid GitHub URLs should be detected."""
        assert is_github_url(url) is True

    @pytest.mark.parametrize("url", [
        "/local/path/to/repo",
        "./relative/path",
        "git@github.com:user/repo.git",  # SSH format not supported yet
        "https://github.com",  # No repo path
        "https://github.com/user",  # No repo name
        "",
        "not-a-url",
    ])
    def test_invalid_github_urls(self, url):
        """Non-GitHub URLs and local paths should not be detected."""
        assert is_github_url(url) is False


class TestNormalizeGithubUrl:
    """Tests for GitHub URL normalization."""

    def test_adds_https_prefix(self):
        """URLs without scheme should get https:// prefix."""
        assert normalize_github_url("github.com/user/repo") == "https://github.com/user/repo"

    def test_preserves_https_prefix(self):
        """URLs with https:// should be preserved."""
        assert normalize_github_url("https://github.com/user/repo") == "https://github.com/user/repo"

    def test_strips_trailing_slash(self):
        """Trailing slashes should be removed."""
        assert normalize_github_url("https://github.com/user/repo/") == "https://github.com/user/repo"

    def test_handles_http(self):
        """HTTP URLs should be preserved (not upgraded)."""
        assert normalize_github_url("http://github.com/user/repo") == "http://github.com/user/repo"


class TestCloneGithubRepo:
    """Tests for GitHub repository cloning."""

    @patch("llmfiles.core.github.subprocess.run")
    def test_successful_clone(self, mock_run, tmp_path):
        """Successful clone should return path to cloned repo."""
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        result = clone_github_repo("https://github.com/user/repo", tmp_path)

        assert result == tmp_path / "repo"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "git" in call_args
        assert "clone" in call_args
        assert "--depth=1" in call_args

    @patch("llmfiles.core.github.subprocess.run")
    def test_clone_failure_raises_git_error(self, mock_run, tmp_path):
        """Failed clone should raise GitError."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="fatal: repository not found",
            stdout=""
        )

        with pytest.raises(GitError) as exc_info:
            clone_github_repo("https://github.com/user/nonexistent", tmp_path)

        assert "repository not found" in str(exc_info.value)

    @patch("llmfiles.core.github.subprocess.run")
    def test_git_not_found_raises_git_error(self, mock_run, tmp_path):
        """Missing git command should raise GitError."""
        mock_run.side_effect = FileNotFoundError()

        with pytest.raises(GitError) as exc_info:
            clone_github_repo("https://github.com/user/repo", tmp_path)

        assert "git command not found" in str(exc_info.value)

    @patch("llmfiles.core.github.subprocess.run")
    def test_normalizes_url(self, mock_run, tmp_path):
        """URL should be normalized before cloning."""
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        clone_github_repo("github.com/user/repo", tmp_path)

        call_args = mock_run.call_args[0][0]
        assert "https://github.com/user/repo" in call_args
