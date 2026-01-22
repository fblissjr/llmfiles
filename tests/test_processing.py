# tests/test_processing.py
"""Tests for file processing and chunking strategies."""

import pytest
from pathlib import Path
from click.testing import CliRunner

from llmfiles.core.processing import process_file_content_to_elements
from llmfiles.config.settings import PromptConfig, ChunkStrategy
from llmfiles.cli.interface import main_cli_group
from llmfiles.structured_processing import ast_utils

ast_utils.load_language_configs_for_llmfiles()


class TestDefaultChunkStrategy:
    """Tests that default chunk strategy is FILE (not STRUCTURE)."""

    def test_default_config_uses_file_strategy(self, tmp_path):
        """PromptConfig should default to FILE chunk strategy."""
        (tmp_path / "test.py").write_text("def foo(): pass")
        config = PromptConfig(input_paths=[tmp_path / "test.py"])
        assert config.chunk_strategy == ChunkStrategy.FILE

    def test_cli_default_is_file_strategy(self, tmp_path):
        """CLI without --chunk-strategy should use file mode."""
        runner = CliRunner()
        with runner.isolated_filesystem() as td:
            proj_dir = Path(td)
            # Create a Python file with a function
            (proj_dir / "module.py").write_text(
                "def my_function():\n    return 42\n"
            )

            result = runner.invoke(
                main_cli_group,
                ["module.py", "--format", "verbose"],  # Use verbose to check element type
                catch_exceptions=False
            )

            assert result.exit_code == 0
            # In file mode (verbose format), element type is "file"
            assert "element type: file" in result.output
            # Should NOT show separate function elements
            assert "element type: function" not in result.output


class TestFileChunkStrategy:
    """Tests for FILE chunk strategy."""

    def test_file_strategy_treats_file_as_single_element(self, tmp_path):
        """File strategy should output the whole file as one element."""
        test_file = tmp_path / "module.py"
        test_file.write_text(
            "class MyClass:\n"
            "    def method(self):\n"
            "        pass\n"
            "\n"
            "def standalone():\n"
            "    pass\n"
        )
        config = PromptConfig(
            input_paths=[test_file],
            chunk_strategy=ChunkStrategy.FILE,
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 1
        assert elements[0]["element_type"] == "file"
        assert "class MyClass" in elements[0]["raw_content"]
        assert "def standalone" in elements[0]["raw_content"]


class TestStructureChunkStrategy:
    """Tests for STRUCTURE chunk strategy."""

    def test_structure_strategy_extracts_functions(self, tmp_path):
        """Structure strategy should extract function definitions."""
        test_file = tmp_path / "module.py"
        test_file.write_text(
            "def func_one():\n"
            "    return 1\n"
            "\n"
            "def func_two():\n"
            "    return 2\n"
        )
        config = PromptConfig(
            input_paths=[test_file],
            chunk_strategy=ChunkStrategy.STRUCTURE,
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 2
        assert all(el["element_type"] == "function" for el in elements)
        names = [el["name"] for el in elements]
        assert "func_one" in names
        assert "func_two" in names

    def test_structure_strategy_extracts_classes(self, tmp_path):
        """Structure strategy should extract class definitions."""
        test_file = tmp_path / "module.py"
        test_file.write_text(
            "class MyClass:\n"
            "    def method_one(self):\n"
            "        pass\n"
            "\n"
            "    def method_two(self):\n"
            "        pass\n"
        )
        config = PromptConfig(
            input_paths=[test_file],
            chunk_strategy=ChunkStrategy.STRUCTURE,
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        # Should have 1 class element (methods are embedded, not separate)
        assert len(elements) == 1
        assert elements[0]["element_type"] == "class"
        assert elements[0]["name"] == "MyClass"
        # Verify methods are in the class source, not extracted separately
        assert "def method_one" in elements[0]["source_code"]
        assert "def method_two" in elements[0]["source_code"]

    def test_structure_strategy_no_duplicate_methods(self, tmp_path):
        """Structure strategy should NOT extract methods as separate elements."""
        test_file = tmp_path / "module.py"
        test_file.write_text(
            "class MyClass:\n"
            "    def method_one(self):\n"
            "        pass\n"
            "\n"
            "def standalone():\n"
            "    pass\n"
        )
        config = PromptConfig(
            input_paths=[test_file],
            chunk_strategy=ChunkStrategy.STRUCTURE,
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        # Should have 2 elements: 1 class + 1 standalone function
        # NOT 3 (class + method + function) which was the old bug
        assert len(elements) == 2
        element_types = {el["element_type"] for el in elements}
        assert element_types == {"class", "function"}

        # Verify no "method" element types
        assert not any(el["element_type"] == "method" for el in elements)


class TestFileSizeFiltering:
    """Tests for max file size filtering."""

    def test_skips_oversized_files(self, tmp_path):
        """Files larger than max_file_size should be skipped."""
        test_file = tmp_path / "large.py"
        test_file.write_text("x" * 1000)  # 1000 bytes
        config = PromptConfig(
            input_paths=[test_file],
            max_file_size=500,  # Only 500 bytes allowed
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 0

    def test_includes_files_under_limit(self, tmp_path):
        """Files smaller than max_file_size should be included."""
        test_file = tmp_path / "small.py"
        test_file.write_text("x" * 100)  # 100 bytes
        config = PromptConfig(
            input_paths=[test_file],
            max_file_size=500,  # 500 bytes allowed
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 1


class TestBinaryFileHandling:
    """Tests for binary file detection and handling."""

    def test_skips_binary_files_by_default(self, tmp_path):
        """Binary files should be skipped when exclude_binary is True."""
        test_file = tmp_path / "binary.bin"
        # Use invalid UTF-8 sequences that produce replacement characters
        # 0x80-0xBF are continuation bytes, invalid at start of sequence
        test_file.write_bytes(b"\x80\x81\x82\x83")
        config = PromptConfig(
            input_paths=[test_file],
            exclude_binary=True,
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 0

    def test_includes_binary_when_flag_set(self, tmp_path):
        """Binary files should be included when exclude_binary is False."""
        test_file = tmp_path / "binary.bin"
        test_file.write_bytes(b"\x00\x01\x02\x03")  # Binary content
        config = PromptConfig(
            input_paths=[test_file],
            exclude_binary=False,
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        # File is included (might have replacement chars)
        assert len(elements) == 1


class TestEmptyFileHandling:
    """Tests for empty file handling."""

    def test_skips_empty_files(self, tmp_path):
        """Empty files should be skipped."""
        test_file = tmp_path / "empty.py"
        test_file.write_text("")
        config = PromptConfig(
            input_paths=[test_file],
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 0

    def test_skips_whitespace_only_files(self, tmp_path):
        """Files with only whitespace should be skipped."""
        test_file = tmp_path / "whitespace.py"
        test_file.write_text("   \n\n\t\t\n   ")
        config = PromptConfig(
            input_paths=[test_file],
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 0


class TestCompactOutputFormat:
    """Tests for compact output format (LLM-optimized)."""

    def test_compact_format_has_file_index_table(self, tmp_path):
        """Compact format should include a markdown file index table."""
        runner = CliRunner()
        with runner.isolated_filesystem() as td:
            proj_dir = Path(td)
            (proj_dir / "module.py").write_text("def foo(): pass\n")

            result = runner.invoke(
                main_cli_group,
                ["module.py", "--format", "compact"],
                catch_exceptions=False
            )

            assert result.exit_code == 0
            # Check for file index table headers
            assert "## Files" in result.output
            assert "| File | Size | Lines | Description |" in result.output
            assert "module.py" in result.output

    def test_compact_format_has_code_section(self, tmp_path):
        """Compact format should have a Code section with file content."""
        runner = CliRunner()
        with runner.isolated_filesystem() as td:
            proj_dir = Path(td)
            (proj_dir / "module.py").write_text("def foo(): pass\n")

            result = runner.invoke(
                main_cli_group,
                ["module.py", "--format", "compact"],
                catch_exceptions=False
            )

            assert result.exit_code == 0
            assert "## Code" in result.output
            assert "### module.py" in result.output
            assert "def foo(): pass" in result.output

    def test_compact_format_is_default(self, tmp_path):
        """Compact format should be the default (no --format flag needed)."""
        runner = CliRunner()
        with runner.isolated_filesystem() as td:
            proj_dir = Path(td)
            (proj_dir / "module.py").write_text("def foo(): pass\n")

            result = runner.invoke(
                main_cli_group,
                ["module.py"],  # No --format specified
                catch_exceptions=False
            )

            assert result.exit_code == 0
            # Compact format markers
            assert "## Files" in result.output
            assert "## Code" in result.output
            # Verbose format markers should NOT be present
            assert "element type:" not in result.output
            assert "source file:" not in result.output

    def test_verbose_format_has_element_metadata(self, tmp_path):
        """Verbose format should include detailed element metadata."""
        runner = CliRunner()
        with runner.isolated_filesystem() as td:
            proj_dir = Path(td)
            (proj_dir / "module.py").write_text("def foo(): pass\n")

            result = runner.invoke(
                main_cli_group,
                ["module.py", "--format", "verbose"],
                catch_exceptions=False
            )

            assert result.exit_code == 0
            assert "source file: module.py" in result.output
            assert "element type: file" in result.output
            assert "lines: 1-" in result.output


class TestModuleDescription:
    """Tests for module docstring extraction."""

    def test_extracts_module_docstring(self, tmp_path):
        """Module docstring should be extracted as description."""
        test_file = tmp_path / "module.py"
        test_file.write_text(
            '"""This module does something useful."""\n'
            'def foo(): pass\n'
        )
        config = PromptConfig(
            input_paths=[test_file],
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 1
        assert elements[0]["description"] == "This module does something useful."

    def test_extracts_first_line_of_multiline_docstring(self, tmp_path):
        """Only first line of multiline docstring should be extracted."""
        test_file = tmp_path / "module.py"
        test_file.write_text(
            '"""First line summary.\n\nMore details here.\n"""\n'
            'def foo(): pass\n'
        )
        config = PromptConfig(
            input_paths=[test_file],
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 1
        assert elements[0]["description"] == "First line summary."

    def test_no_description_when_no_docstring(self, tmp_path):
        """Files without module docstring should have None description."""
        test_file = tmp_path / "module.py"
        test_file.write_text("def foo(): pass\n")
        config = PromptConfig(
            input_paths=[test_file],
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 1
        assert elements[0]["description"] is None

    def test_non_python_has_no_description(self, tmp_path):
        """Non-Python files should have None description."""
        test_file = tmp_path / "module.js"
        test_file.write_text("// JavaScript file\nconst x = 1;\n")
        config = PromptConfig(
            input_paths=[test_file],
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 1
        assert elements[0]["description"] is None


class TestElementLineCount:
    """Tests for line_count field in elements."""

    def test_element_has_line_count(self, tmp_path):
        """Elements should include a line_count field."""
        test_file = tmp_path / "module.py"
        test_file.write_text("def foo():\n    pass\n")
        config = PromptConfig(
            input_paths=[test_file],
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 1
        assert elements[0]["line_count"] == 2

    def test_line_count_matches_content(self, tmp_path):
        """line_count should match actual content lines."""
        test_file = tmp_path / "module.py"
        content = "line1\nline2\nline3\nline4\nline5\n"
        test_file.write_text(content)
        config = PromptConfig(
            input_paths=[test_file],
            base_dir=tmp_path,
        )

        elements = process_file_content_to_elements(test_file, config)

        assert len(elements) == 1
        assert elements[0]["line_count"] == 5
