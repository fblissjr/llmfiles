import pytest
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner
from llmfiles.cli.interface import main_cli_group
from llmfiles.structured_processing import ast_utils

ast_utils.load_language_configs_for_llmfiles()

# For testing external dependency detection, we need to include test packages
# in the installed packages set that the resolver checks against.
TEST_INSTALLED_PACKAGES = {
    "click", "pathspec", "rich", "structlog",
    "tree-sitter", "tree-sitter-language-pack",
    "numpy",  # Added for test files that import numpy
}

@pytest.fixture
def cli_project(tmp_path: Path):
    """Creates a mock project for testing the CLI end-to-end."""
    proj_dir = tmp_path / "cli_proj"
    proj_dir.mkdir()

    (proj_dir / "helpers.py").write_text("def helper_func():\n    return 'I am a helper with a MAGIC_KEYWORD'\n")
    (proj_dir / "utils.py").write_text("import numpy\nfrom helpers import helper_func\n\ndef util_func():\n    return helper_func()\n")
    (proj_dir / "main.py").write_text("from utils import util_func\n\nif __name__ == '__main__':\n    util_func()\n")
    (proj_dir / "unrelated.py").write_text("# This file should not be included.")

    return proj_dir

@patch("llmfiles.core.pipeline.INSTALLED_PACKAGES", TEST_INSTALLED_PACKAGES)
def test_cli_end_to_end_dependency_resolution():
    """
    Tests the full CLI with dependency resolution starting from a single file.
    """
    runner = CliRunner()
    with runner.isolated_filesystem() as td:
        proj_dir = Path(td)
        (proj_dir / "helpers.py").write_text("def helper_func(): return 'helper'")
        (proj_dir / "utils.py").write_text("import numpy\nfrom helpers import helper_func\n\ndef util_func(): return helper_func()")
        (proj_dir / "main.py").write_text("from utils import util_func\n\nif __name__ == '__main__': util_func()")
        (proj_dir / "unrelated.py").write_text("# Not imported")

        result = runner.invoke(
            main_cli_group,
            ["main.py", "--recursive", "--external-deps", "metadata", "--format", "verbose"],
            catch_exceptions=False
        )

        assert result.exit_code == 0
        output = result.output
        assert "source file: main.py" in output
        assert "source file: utils.py" in output
        assert "source file: helpers.py" in output
        assert "source file: unrelated.py" not in output
        assert "external dependencies:" in output
        assert "- numpy" in output

@patch("llmfiles.core.pipeline.INSTALLED_PACKAGES", TEST_INSTALLED_PACKAGES)
def test_cli_end_to_end_grep_seed():
    """
    Tests the full CLI using --grep-content to seed the dependency resolution.
    """
    runner = CliRunner()
    with runner.isolated_filesystem() as td:
        proj_dir = Path(td)
        (proj_dir / "helpers.py").write_text("def helper_func(): return 'helper'")
        (proj_dir / "utils.py").write_text("import numpy\nfrom helpers import helper_func\n\ndef util_func(): return helper_func()")
        (proj_dir / "main.py").write_text("from utils import util_func\n# MAGIC_KEYWORD\n\nif __name__ == '__main__': util_func()")
        (proj_dir / "unrelated.py").write_text("# Not imported")

        result = runner.invoke(
            main_cli_group,
            [".", "--recursive", "--grep-content", "MAGIC_KEYWORD", "--external-deps", "metadata", "--format", "verbose"],
            catch_exceptions=False
        )

        assert result.exit_code == 0
        output = result.output
        assert "source file: main.py" in output
        assert "source file: utils.py" in output
        assert "source file: helpers.py" in output
        assert "source file: unrelated.py" not in output
        assert "external dependencies:" in output
        assert "- numpy" in output
