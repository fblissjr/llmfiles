from pathlib import Path

import pytest

from llmfiles.core.discovery.pattern_expansion import expand_user_patterns


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1\n")
    (tmp_path / "CHANGELOG.md").write_text("# changelog\n")
    (tmp_path / "uv.lock").write_text("")
    return tmp_path


def test_bare_extension_expands_to_recursive_glob(project: Path):
    assert expand_user_patterns(["py"], project) == ["**/*.py"]


def test_multiple_bare_extensions_via_repeated_flag(project: Path):
    assert expand_user_patterns(["py", "md"], project) == ["**/*.py", "**/*.md"]


def test_comma_separated_patterns_split(project: Path):
    assert expand_user_patterns(["py,md"], project) == ["**/*.py", "**/*.md"]


def test_comma_separated_with_whitespace(project: Path):
    assert expand_user_patterns(["py, md , txt"], project) == [
        "**/*.py",
        "**/*.md",
        "**/*.txt",
    ]


def test_existing_directory_expands_to_glob(project: Path):
    assert expand_user_patterns(["scripts"], project) == ["scripts/**"]


def test_directory_with_trailing_slash_expands(project: Path):
    assert expand_user_patterns(["scripts/"], project) == ["scripts/**"]


def test_multiple_directories_via_comma(project: Path):
    assert expand_user_patterns(["scripts,tests"], project) == [
        "scripts/**",
        "tests/**",
    ]


def test_filename_with_extension_passes_through(project: Path):
    # gitignore-style matching already finds CHANGELOG.md at any depth.
    assert expand_user_patterns(["CHANGELOG.md"], project) == ["CHANGELOG.md"]


def test_explicit_glob_passes_through(project: Path):
    assert expand_user_patterns(["**/*.py"], project) == ["**/*.py"]


def test_glob_with_brackets_passes_through(project: Path):
    assert expand_user_patterns(["src/[a-z]*.py"], project) == ["src/[a-z]*.py"]


def test_empty_input_returns_empty(project: Path):
    assert expand_user_patterns([], project) == []


def test_empty_string_pieces_dropped(project: Path):
    assert expand_user_patterns(["py,,md", ""], project) == ["**/*.py", "**/*.md"]


def test_nonexistent_bare_word_treated_as_extension(project: Path):
    # "xyz" is not a directory and has no dot, so we infer extension.
    assert expand_user_patterns(["xyz"], project) == ["**/*.xyz"]


def test_nested_directory_path_expands(project: Path):
    (project / "a" / "b").mkdir(parents=True)
    assert expand_user_patterns(["a/b"], project) == ["a/b/**"]


def test_dotfile_passes_through(project: Path):
    # Leading-dot tokens (e.g., ".env") should match by name, not be treated as ext.
    assert expand_user_patterns([".env"], project) == [".env"]
