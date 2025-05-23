# llmfiles/tests/test_cli_profiles_and_save.py
"""
Tests for CLI features related to configuration profiles, pattern files,
saving profiles, and header path display.
"""
import pytest
import toml
from pathlib import Path
from click.testing import CliRunner

from llmfiles.cli import main_cli_group # Adjust import if your entry point is different

@pytest.fixture
def sample_llmfiles_toml(tmp_path: Path, pattern_files_dir: Path) -> Path:
    """
    Creates a sample .llmfiles.toml file in a temporary directory
    with global settings and various profiles for testing.
    It also creates the referenced pattern files.
    """
    config_dir = tmp_path / "toml_project_root"
    config_dir.mkdir()

    # Create dummy pattern files referenced by the TOML
    patterns_sub_dir = config_dir / "test_patterns" # Store pattern files in a subdir for clarity
    patterns_sub_dir.mkdir(exist_ok=True)
    (patterns_sub_dir / "generic_py.txt").write_text("*.py")
    (patterns_sub_dir / "specific_exclude.txt").write_text("core/models.py\nutils.py")
    (patterns_sub_dir / "docs_includes.txt").write_text("docs/**/*.md")


    toml_content = f"""
# Global settings in .llmfiles.toml
output_format = "xml" # Global default output format
line_numbers = true
show_absolute_project_path = false # Global default for header path

# Profile definitions
[profiles.python_essentials]
description = "Only Python files, no tests or hidden files."
include_patterns = ["**/*.py"]
exclude_patterns = ["**/tests/**", ".*/**"] # Exclude hidden files/dirs starting with dot
line_numbers = true # Override global default potentially
show_absolute_project_path = true # Override global

[profiles.docs_only]
description = "Only Markdown files from the docs_includes.txt pattern file."
include_from_files = ["{str(patterns_sub_dir / 'docs_includes.txt')}"] # Path to pattern file
exclude_patterns = ["README.md"] # Example of excluding a specific doc file
output_format = "markdown" # Override global

[profiles.complex_python_selection]
description = "Python files via file, with specific excludes also from file."
include_from_files = ["{str(patterns_sub_dir / 'generic_py.txt')}"]
exclude_from_files = ["{str(patterns_sub_dir / 'specific_exclude.txt')}"]
# Inherits global line_numbers = true and show_absolute_project_path = false

[profiles.all_sources_no_header_abs_path]
description = "All source files, explicit relative path in header."
include_patterns = ["**/*.py", "**/*.js", "**/*.rs"] # Example for multiple source types
show_absolute_project_path = false # Explicitly false for this profile
sort = "name_asc"

[profiles.empty_patterns_test]
description = "Test profile with explicitly empty pattern lists."
include_patterns = []
exclude_patterns = []
include_from_files = []
exclude_from_files = []

[profiles.git_diff_profile]
description = "Focus on git diff."
git_diff = true
# other settings could be added here if needed for specific diff output
"""
    toml_file_path = config_dir / ".llmfiles.toml"
    toml_file_path.write_text(toml_content)
    
    # Also create some dummy project files in this config_dir for tests to act upon
    create_project_structure(config_dir, {
        "app.py": "print('app')",
        "utils.py": "# util functions",
        "core/models.py": "# core models",
        "core/service.py": "# core service",
        "docs/index.md": "# Main Docs",
        "docs/api/reference.md": "# API Ref",
        "tests/test_app.py": "# app tests",
        "README.md": "# Project Readme",
    })
    
    return toml_file_path

# Helper function to create a dummy project structure
def create_project_structure(base_path: Path, files_to_create: dict):
    """
    Creates a directory structure with files.
    files_to_create = {"dir/file.py": "content", "another.txt": "text"}
    """
    for rel_path, content in files_to_create.items():
        file_path = base_path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content or f"content of {rel_path}")

@pytest.fixture
def runner():
    return CliRunner()

@pytest.fixture
def temp_project_dir(tmp_path: Path) -> Path:
    """Creates a temporary project directory for testing."""
    project_dir = tmp_path / "sample_project"
    project_dir.mkdir()
    create_project_structure(project_dir, {
        "main.py": "print('hello main')",
        "utils.py": "def helper(): pass",
        "core/logic.py": "class CoreLogic: pass",
        "core/models.py": "class DataModel: pass",
        "tests/test_main.py": "assert True",
        "docs/readme.md": "# Project Readme",
        "docs/usage.md": "How to use.",
        "data/config.json": "{}",
        ".hiddenfile": "secret",
        "NOTES.txt": "some notes",
        "sub/another.py": "print('sub another')"
    })
    return project_dir

@pytest.fixture
def pattern_files_dir(tmp_path: Path) -> Path:
    """Creates a directory with sample pattern files."""
    patterns_dir = tmp_path / "patterns"
    patterns_dir.mkdir()
    (patterns_dir / "python_includes.txt").write_text("**/*.py\n# This is a comment\n")
    (patterns_dir / "md_includes.txt").write_text("*.md")
    (patterns_dir / "core_excludes.txt").write_text("core/*\n")
    (patterns_dir / "test_excludes.txt").write_text("**/tests/**")
    return patterns_dir

# --- Tests for Header Path Display ---

def test_header_relative_path_default(runner: CliRunner, temp_project_dir: Path):
    """Test that header shows relative project path by default."""
    result = runner.invoke(main_cli_group, ["-in", str(temp_project_dir), "main.py"])
    assert result.exit_code == 0
    # Check for project root display name, not the full absolute path in the main header line
    assert f"project root: {temp_project_dir.name}" in result.output
    assert "(full absolute path:" not in result.output.splitlines()[1] # Assuming header is near top

def test_header_absolute_path_with_cli_flag(runner: CliRunner, temp_project_dir: Path):
    """Test --show-abs-project-path CLI flag."""
    result = runner.invoke(main_cli_group, ["-in", str(temp_project_dir), "main.py", "--show-abs-project-path"])
    assert result.exit_code == 0
    assert f"project root: {temp_project_dir.name}" in result.output # Name still there
    assert f"(full absolute path: {str(temp_project_dir.resolve())})" in result.output

# Example modification for test_header_absolute_path_from_profile
def test_header_absolute_path_from_profile(runner: CliRunner, temp_project_dir: Path): # temp_project_dir is now the root for isolated_filesystem
    config_content = """
show_absolute_project_path = true
[profiles.abs_path_test]
include_patterns = ["main.py"] 
    """
    # Create a structure within the isolated filesystem
    with runner.isolated_filesystem(temp_dir=temp_project_dir) as isolated_dir_path_obj:
        isolated_dir = Path(isolated_dir_path_obj)
        
        # Create .llmfiles.toml in this isolated directory
        config_file = isolated_dir / ".llmfiles.toml"
        config_file.write_text(config_content)
        
        # Create the dummy files llmfiles will process, also in this isolated dir
        (isolated_dir / "main.py").write_text("print('main content')")

        # llmfiles will run with its CWD as isolated_dir
        result = runner.invoke(main_cli_group, ["--config-profile", "abs_path_test", "--input-path", "."], # -in . now refers to isolated_dir
                               catch_exceptions=False)
        
        assert result.exit_code == 0
        # project_root_display_name should be the name of the isolated_dir (e.g., the temp name)
        # or "." if that's how base_dir.name behaves for CWD.
        # Let's check the actual path for robustness.
        assert f"(full absolute path: {str(isolated_dir.resolve())})" in result.output


# --- Tests for Pattern Files and Profiles ---

def test_profile_with_direct_includes_excludes(runner: CliRunner, temp_project_dir: Path):
    config_content = """
[profiles.py_only_no_core]
include_patterns = ["**/*.py"]
exclude_patterns = ["core/**", "**/tests/**"]
    """
    # Use isolated_filesystem to ensure CWD is where .llmfiles.toml is created
    with runner.isolated_filesystem(temp_dir=temp_project_dir) as isolated_dir_path:
        isolated_dir = Path(isolated_dir_path)
        
        # Create .llmfiles.toml in this isolated directory
        (isolated_dir / ".llmfiles.toml").write_text(config_content)
        
        # Create dummy files based on the original temp_project_dir structure
        # but inside the isolated_dir for this test run.
        create_project_structure(isolated_dir, { # Assuming files from original temp_project_dir fixture
            "main.py": "print('hello main')", "utils.py": "def helper(): pass",
            "core/logic.py": "class CoreLogic: pass", "core/models.py": "class DataModel: pass",
            "tests/test_main.py": "assert True", "sub/another.py": "print('sub another')"
        })

        # llmfiles runs with CWD as isolated_dir. "-in ." refers to isolated_dir.
        result = runner.invoke(main_cli_group, ["-in", ".", "--config-profile", "py_only_no_core"])
        
        assert result.exit_code == 0
        assert "main.py" in result.output
        assert "utils.py" in result.output
        assert "sub/another.py" in result.output
        assert "core/logic.py" not in result.output
        assert "core/models.py" not in result.output
        assert "tests/test_main.py" not in result.output
        assert "docs/readme.md" not in result.output

def test_profile_with_include_from_files(runner: CliRunner, temp_project_dir: Path, pattern_files_dir: Path):
    """Test profile using include_from_files."""
    config_content = f"""
[profiles.py_and_md_from_files]
include_from_files = [
    "{str(pattern_files_dir / 'python_includes.txt')}",
    "{str(pattern_files_dir / 'md_includes.txt')}"
]
    """
    (temp_project_dir / ".llmfiles.toml").write_text(config_content)
    result = runner.invoke(main_cli_group, ["-in", str(temp_project_dir), "--config-profile", "py_and_md_from_files"])

    assert result.exit_code == 0
    assert "main.py" in result.output
    assert "utils.py" in result.output
    assert "core/logic.py" in result.output # python_includes.txt is **/*.py
    assert "docs/readme.md" in result.output # md_includes.txt is *.md
    assert "docs/usage.md" in result.output
    assert "NOTES.txt" not in result.output # Not .py or .md

def test_profile_with_exclude_from_files(runner: CliRunner, temp_project_dir: Path, pattern_files_dir: Path):
    """Test profile using exclude_from_files."""
    config_content = f"""
[profiles.all_except_core_and_tests]
include_patterns = ["**/*"] # Include everything initially
exclude_from_files = [
    "{str(pattern_files_dir / 'core_excludes.txt')}",
    "{str(pattern_files_dir / 'test_excludes.txt')}"
]
    """
    (temp_project_dir / ".llmfiles.toml").write_text(config_content)
    result = runner.invoke(main_cli_group, ["-in", str(temp_project_dir), "--config-profile", "all_except_core_and_tests"])
    
    assert result.exit_code == 0
    assert "main.py" in result.output # Not in core or tests
    assert "core/logic.py" not in result.output
    assert "tests/test_main.py" not in result.output
    assert "docs/readme.md" in result.output # Not in core or tests

def test_profile_with_mixed_patterns(runner: CliRunner, temp_project_dir: Path, pattern_files_dir: Path):
    """Test profile using direct patterns and patterns from files."""
    config_content = f"""
[profiles.mixed_config]
include_patterns = ["NOTES.txt"] # Direct include
include_from_files = ["{str(pattern_files_dir / 'md_includes.txt')}"]
exclude_patterns = ["utils.py"] # Direct exclude
exclude_from_files = ["{str(pattern_files_dir / 'core_excludes.txt')}"]
    """
    (temp_project_dir / ".llmfiles.toml").write_text(config_content)
    result = runner.invoke(main_cli_group, ["-in", str(temp_project_dir), "--config-profile", "mixed_config"])

    assert result.exit_code == 0
    assert "NOTES.txt" in result.output      # From direct include
    assert "docs/readme.md" in result.output # From md_includes.txt
    assert "docs/usage.md" in result.output  # From md_includes.txt
    assert "main.py" not in result.output    # Not included
    assert "utils.py" not in result.output   # Excluded directly
    assert "core/logic.py" not in result.output # Excluded via file

# --- Tests for --save Profile Feature ---

def test_save_profile_creates_and_populates_toml(runner: CliRunner, temp_project_dir: Path):
    """Test --save creates .llmfiles.toml and saves options to a new profile."""
    result = runner.invoke(main_cli_group, [
        "-in", str(temp_project_dir),
        "--include", "**/*.py",
        "--exclude", "**/tests/**",
        "--line-numbers",
        "--show-abs-project-path", # Test saving this new flag
        "--save", "my_saved_profile"
    ], catch_exceptions=False) # Easier to debug if test fails

    assert result.exit_code == 0
    assert "Configuration saved" in result.stderr # Check stderr for confirmation message
    
    toml_file = temp_project_dir / ".llmfiles.toml"
    assert toml_file.exists()
    
    saved_config = toml.load(toml_file)
    assert "profiles" in saved_config
    assert "my_saved_profile" in saved_config["profiles"]
    
    profile = saved_config["profiles"]["my_saved_profile"]
    assert profile["include"] == ["**/*.py"] # Checks mapping to TOML key
    assert profile["exclude"] == ["**/tests/**"]
    assert profile["line_numbers"] is True
    assert profile["show_absolute_project_path"] is True # Check new flag saved
    assert "input_paths" not in profile # Should skip if it was default CWD effectively

def test_save_profile_to_default(runner: CliRunner, temp_project_dir: Path):
    """Test --save DEFAULT saves to top-level in TOML."""
    (temp_project_dir / "dummy.py").write_text("pass") # ensure a file exists for input_paths
    result = runner.invoke(main_cli_group, [
        "--input-path", str(temp_project_dir / "dummy.py"), # Non-default input_path
        "--output-format", "xml",
        "--save", "DEFAULT"
    ])
    assert result.exit_code == 0
    
    toml_file = temp_project_dir / ".llmfiles.toml"
    assert toml_file.exists()
    
    saved_config = toml.load(toml_file)
    assert "profiles" not in saved_config or not saved_config["profiles"] # No profiles section or empty
    assert saved_config["output_format"] == "xml"
    assert saved_config["input_paths"] == [str(temp_project_dir / "dummy.py")]

def test_save_profile_updates_existing_profile(runner: CliRunner, temp_project_dir: Path):
    """Test --save updates an existing profile correctly."""
    initial_config_content = """
[profiles.updater_test]
line_numbers = false
include_patterns = ["old/*.txt"] 
    """
    toml_file = temp_project_dir / ".llmfiles.toml"
    toml_file.write_text(initial_config_content)

    result = runner.invoke(main_cli_group, [
        "-in", str(temp_project_dir),
        "--line-numbers", # This will be true, overriding false
        "--include", "new/*.py", # This will replace include_patterns
        "--sort", "date_desc", # New setting for the profile
        "--save", "updater_test"
    ])
    assert result.exit_code == 0
    
    updated_config = toml.load(toml_file)
    profile = updated_config["profiles"]["updater_test"]
    assert profile["line_numbers"] is True
    assert profile["include"] == ["new/*.py"]
    assert profile["sort"] == "date_desc"

def test_save_profile_with_pattern_files(runner: CliRunner, temp_project_dir: Path, pattern_files_dir: Path):
    """Test saving a profile that uses --include-from-file and --exclude-from-file."""
    include_file_path = pattern_files_dir / "python_includes.txt"
    exclude_file_path = pattern_files_dir / "core_excludes.txt"

    result = runner.invoke(main_cli_group, [
        "-in", str(temp_project_dir),
        "--include-from-file", str(include_file_path),
        "--exclude-from-file", str(exclude_file_path),
        "--save", "profile_with_files"
    ])
    assert result.exit_code == 0
    
    toml_file = temp_project_dir / ".llmfiles.toml"
    assert toml_file.exists()
    
    saved_config = toml.load(toml_file)
    profile = saved_config["profiles"]["profile_with_files"]
    assert profile["include_from_files"] == [str(include_file_path)]
    assert profile["exclude_from_files"] == [str(exclude_file_path)]

def test_save_profile_omits_defaults(runner: CliRunner, temp_project_dir: Path):
    """Test that --save omits options that are already default, unless explicitly needed."""
    # CLI options here are mostly defaults for PromptConfig
    result = runner.invoke(main_cli_group, [
        "-in", str(temp_project_dir), # Non-default if project_dir is not "." relative to test exec
        "--output-format", "markdown", # Default
        "--no-codeblock", # Default is false, so this is false, should be omitted if default is false
        "--save", "minimal_profile"
    ])
    assert result.exit_code == 0
    toml_file = temp_project_dir / ".llmfiles.toml"
    saved_config = toml.load(toml_file)
    profile = saved_config["profiles"]["minimal_profile"]

    # Check for what *should* be there
    assert "input_paths" in profile # Because it's likely non-default relative to test exec
    
    # Check for what *should NOT* be there (because it's default)
    # This depends on the refined logic in _save_options_to_profile
    assert "output_format" not in profile # Default is markdown
    assert "no_codeblock" not in profile   # Default is false
    assert "line_numbers" not in profile   # Default is false
    # Note: This test is sensitive to the exact default values in PromptConfig and the save logic.

def test_load_profile_from_sample_toml(runner: CliRunner, sample_llmfiles_toml: Path):
    """
    Tests loading a profile from the comprehensive sample TOML file.
    Focuses on 'python_essentials' profile.
    """
    project_root = sample_llmfiles_toml.parent # The directory containing .llmfiles.toml

    # Important: CliRunner runs commands from the current working directory of the test runner.
    # We need to change the CWD for the llmfiles process to where .llmfiles.toml is.
    # The `with runner.isolated_filesystem(temp_dir=project_root):` can also be used
    # or by passing `cwd` to invoke if supported, but changing CWD for the test itself is often simplest.
    
    import os
    original_cwd = Path.cwd()
    try:
        os.chdir(project_root) # Change CWD to where .llmfiles.toml is

        # Run llmfiles using a profile from the sample_llmfiles_toml
        # The `-in .` will now correctly refer to `project_root`
        result = runner.invoke(main_cli_group, ["-in", ".", "--config-profile", "python_essentials"], catch_exceptions=False)
        
        assert result.exit_code == 0
        # Check for files expected from 'python_essentials' profile
        assert "app.py" in result.output       # Included by **/*.py
        assert "utils.py" in result.output     # Included by **/*.py
        assert "core/service.py" in result.output # Included by **/*.py
        assert "core/models.py" in result.output # Included by **/*.py
        
        # Check for files/patterns excluded by 'python_essentials'
        assert "tests/test_app.py" not in result.output # Excluded by **/tests/**
        assert "docs/index.md" not in result.output     # Not a .py file

        # Check for header path setting from 'python_essentials' profile
        assert f"project root: {project_root.name}" in result.output
        assert f"(full absolute path: {str(project_root.resolve())})" in result.output # show_absolute_project_path = true

    finally:
        os.chdir(original_cwd) # Restore original CWD


def test_load_profile_with_pattern_files_from_sample_toml(runner: CliRunner, sample_llmfiles_toml: Path):
    """Tests loading 'docs_only' profile which uses include_from_files."""
    project_root = sample_llmfiles_toml.parent
    import os
    original_cwd = Path.cwd()
    try:
        os.chdir(project_root)
        result = runner.invoke(main_cli_group, ["-in", ".", "--config-profile", "docs_only"])
        assert result.exit_code == 0
        
        assert "docs/index.md" in result.output # From docs_includes.txt -> docs/**/*.md
        assert "docs/api/reference.md" in result.output
        assert "README.md" not in result.output # Excluded directly in profile
        assert "app.py" not in result.output    # Not a doc file
        
        # Check output format from profile
        # This is a bit tricky as output format affects the whole structure.
        # For XML, we'd expect XML tags. For MD (default for this profile), markdown.
        assert "<llmfiles_context" not in result.output # Should be markdown, not global XML
        assert "project root:" in result.output # Markdown style header

    finally:
        os.chdir(original_cwd)