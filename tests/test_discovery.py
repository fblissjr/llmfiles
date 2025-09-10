import pytest
from pathlib import Path
from llmfiles.config.settings import PromptConfig
from llmfiles.core.discovery.walker import grep_files_for_content

@pytest.fixture
def grep_project(tmp_path: Path):
    """Creates a mock project for testing grep functionality."""
    proj_dir = tmp_path / "grep_proj"
    proj_dir.mkdir()

    (proj_dir / "file_with_keyword.txt").write_text("hello this file has the MAGIC_KEYWORD inside.")
    (proj_dir / "another_with_keyword.py").write_text("# python file with MAGIC_KEYWORD")
    (proj_dir / "file_without.txt").write_text("this file is clean.")

    # Create a subdirectory with a file
    (proj_dir / "sub").mkdir()
    (proj_dir / "sub" / "sub_file_with_keyword.txt").write_text("nested MAGIC_KEYWORD here")

    return proj_dir

def test_grep_files_for_content(grep_project: Path):
    # Change current directory to the parent of the grep_project for predictable paths
    import os
    os.chdir(grep_project.parent)

    config = PromptConfig(
        input_paths=[grep_project],
        grep_content_pattern="MAGIC_KEYWORD"
    )

    # Since PromptConfig post_init sets base_dir to cwd, we adjust it for the test
    config.base_dir = grep_project

    found_files = list(grep_files_for_content(config))

    assert len(found_files) == 3

    found_paths = {p.relative_to(grep_project) for p in found_files}
    expected_paths = {
        Path("file_with_keyword.txt"),
        Path("another_with_keyword.py"),
        Path("sub/sub_file_with_keyword.txt")
    }
    assert found_paths == expected_paths

def test_grep_files_no_matches(grep_project: Path):
    import os
    os.chdir(grep_project.parent)

    config = PromptConfig(
        input_paths=[grep_project],
        grep_content_pattern="NOT_A_KEYWORD"
    )
    config.base_dir = grep_project

    found_files = list(grep_files_for_content(config))

    assert len(found_files) == 0
