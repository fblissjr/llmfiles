"""Tests for Jedi-based call tracing."""
import pytest
from pathlib import Path
from llmfiles.core.jedi_tracer import CallTracer, CallInfo


@pytest.fixture
def simple_project(tmp_path: Path):
    """Creates a simple project with two files."""
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()

    # main.py imports and calls helper
    main_py = proj_dir / "main.py"
    main_py.write_text("""
from helper import greet

def main():
    message = greet("World")
    print(message)

if __name__ == "__main__":
    main()
""")

    # helper.py has a simple function
    helper_py = proj_dir / "helper.py"
    helper_py.write_text("""
def greet(name: str) -> str:
    return f"Hello, {name}!"
""")

    return proj_dir


@pytest.fixture
def chain_project(tmp_path: Path):
    """Creates a project with chain of calls: entry -> middle -> leaf."""
    proj_dir = tmp_path / "chain_project"
    proj_dir.mkdir()

    # entry.py -> middle.py
    entry_py = proj_dir / "entry.py"
    entry_py.write_text("""
from middle import process

def run():
    result = process("data")
    return result
""")

    # middle.py -> leaf.py
    middle_py = proj_dir / "middle.py"
    middle_py.write_text("""
from leaf import transform

def process(data):
    return transform(data)
""")

    # leaf.py has no further internal calls
    leaf_py = proj_dir / "leaf.py"
    leaf_py.write_text("""
def transform(data):
    return data.upper()
""")

    return proj_dir


@pytest.fixture
def circular_project(tmp_path: Path):
    """Creates a project with circular imports."""
    proj_dir = tmp_path / "circular_project"
    proj_dir.mkdir()

    # a.py imports b
    a_py = proj_dir / "a.py"
    a_py.write_text("""
from b import func_b

def func_a():
    return func_b()
""")

    # b.py imports a
    b_py = proj_dir / "b.py"
    b_py.write_text("""
from a import func_a

def func_b():
    return "b"
""")

    return proj_dir


@pytest.fixture
def external_deps_project(tmp_path: Path):
    """Creates a project that uses external packages."""
    proj_dir = tmp_path / "ext_project"
    proj_dir.mkdir()

    # main.py uses os (stdlib) and internal module
    main_py = proj_dir / "main.py"
    main_py.write_text("""
import os
from utils import get_path

def main():
    cwd = os.getcwd()
    path = get_path()
    return f"{cwd}/{path}"
""")

    utils_py = proj_dir / "utils.py"
    utils_py.write_text("""
def get_path():
    return "data"
""")

    return proj_dir


class TestCallTracer:
    """Tests for the CallTracer class."""

    def test_is_in_project(self, simple_project: Path):
        """Test that _is_in_project correctly identifies project files."""
        tracer = CallTracer(project_root=simple_project)

        # Files within project should be in project
        assert tracer._is_in_project(simple_project / "main.py")
        assert tracer._is_in_project(simple_project / "helper.py")

        # Non-existent files should not be in project
        assert not tracer._is_in_project(simple_project / "nonexistent.py")

        # Files outside project should not be in project
        assert not tracer._is_in_project(Path("/usr/lib/python/os.py"))

        # None should not be in project
        assert not tracer._is_in_project(None)

    def test_trace_single_file_no_calls(self, tmp_path: Path):
        """Test tracing a file with no internal calls."""
        proj_dir = tmp_path / "single"
        proj_dir.mkdir()

        single_py = proj_dir / "single.py"
        single_py.write_text("""
def standalone():
    return 42
""")

        tracer = CallTracer(project_root=proj_dir)
        discovered = tracer.trace_file(single_py)

        # Should discover no new files
        assert len(discovered) == 0
        # But the file itself should be visited
        assert single_py.resolve() in tracer.visited_files

    def test_trace_file_with_import(self, simple_project: Path):
        """Test tracing a file that imports another."""
        tracer = CallTracer(project_root=simple_project)
        discovered = tracer.trace_file(simple_project / "main.py")

        # Should discover helper.py
        helper_path = (simple_project / "helper.py").resolve()
        assert helper_path in discovered

    def test_trace_all_simple(self, simple_project: Path):
        """Test trace_all with a simple two-file project."""
        tracer = CallTracer(project_root=simple_project)
        all_files = tracer.trace_all([simple_project / "main.py"])

        # Should include both files
        assert len(all_files) == 2
        assert (simple_project / "main.py").resolve() in all_files
        assert (simple_project / "helper.py").resolve() in all_files

    def test_trace_all_chain(self, chain_project: Path):
        """Test trace_all discovers the full chain of dependencies."""
        tracer = CallTracer(project_root=chain_project)
        all_files = tracer.trace_all([chain_project / "entry.py"])

        # Should discover all three files in the chain
        assert len(all_files) == 3
        assert (chain_project / "entry.py").resolve() in all_files
        assert (chain_project / "middle.py").resolve() in all_files
        assert (chain_project / "leaf.py").resolve() in all_files

    def test_trace_all_circular(self, circular_project: Path):
        """Test that circular imports are handled correctly."""
        tracer = CallTracer(project_root=circular_project)
        all_files = tracer.trace_all([circular_project / "a.py"])

        # Should discover both files without infinite loop
        assert len(all_files) == 2
        assert (circular_project / "a.py").resolve() in all_files
        assert (circular_project / "b.py").resolve() in all_files

    def test_trace_excludes_external(self, external_deps_project: Path):
        """Test that external/stdlib modules are not traced."""
        tracer = CallTracer(project_root=external_deps_project)
        all_files = tracer.trace_all([external_deps_project / "main.py"])

        # Should only include internal files, not os module
        assert len(all_files) == 2
        assert (external_deps_project / "main.py").resolve() in all_files
        assert (external_deps_project / "utils.py").resolve() in all_files

        # No external module paths should be present
        for f in all_files:
            assert external_deps_project.resolve() in f.parents or f == external_deps_project.resolve()

    def test_trace_non_python_skipped(self, tmp_path: Path):
        """Test that non-Python files are skipped."""
        proj_dir = tmp_path / "mixed"
        proj_dir.mkdir()

        py_file = proj_dir / "code.py"
        py_file.write_text("x = 1")

        txt_file = proj_dir / "readme.txt"
        txt_file.write_text("This is a readme")

        tracer = CallTracer(project_root=proj_dir)
        discovered = tracer.trace_file(txt_file)

        # Non-Python files should be skipped
        assert len(discovered) == 0
        assert txt_file.resolve() not in tracer.visited_files

    def test_get_call_graph_summary_empty(self, tmp_path: Path):
        """Test call graph summary with no calls."""
        proj_dir = tmp_path / "empty"
        proj_dir.mkdir()

        single_py = proj_dir / "single.py"
        single_py.write_text("x = 1")

        tracer = CallTracer(project_root=proj_dir)
        tracer.trace_all([single_py])

        summary = tracer.get_call_graph_summary()
        # Empty summary when no calls discovered
        assert summary == ""

    def test_get_call_graph_summary_with_calls(self, simple_project: Path):
        """Test call graph summary includes call relationships."""
        tracer = CallTracer(project_root=simple_project)
        tracer.trace_all([simple_project / "main.py"])

        summary = tracer.get_call_graph_summary()

        # Summary should contain key sections
        assert "## Call Graph" in summary
        assert "## Discovered Files" in summary
        assert "main.py" in summary
        assert "helper.py" in summary

    def test_call_info_structure(self, simple_project: Path):
        """Test that discovered calls have correct structure."""
        tracer = CallTracer(project_root=simple_project)
        tracer.trace_all([simple_project / "main.py"])

        # Should have at least one call recorded
        assert len(tracer.discovered_calls) > 0

        call = tracer.discovered_calls[0]
        assert isinstance(call, CallInfo)
        assert isinstance(call.from_file, Path)
        assert isinstance(call.to_file, Path)
        assert isinstance(call.from_line, int)
        assert isinstance(call.to_line, int)


class TestCallTracerEdgeCases:
    """Edge case tests for CallTracer."""

    def test_trace_empty_file(self, tmp_path: Path):
        """Test tracing an empty Python file."""
        proj_dir = tmp_path / "empty_file"
        proj_dir.mkdir()

        empty_py = proj_dir / "empty.py"
        empty_py.write_text("")

        tracer = CallTracer(project_root=proj_dir)
        discovered = tracer.trace_file(empty_py)

        assert len(discovered) == 0
        assert empty_py.resolve() in tracer.visited_files

    def test_trace_syntax_error_file(self, tmp_path: Path):
        """Test tracing a file with syntax errors."""
        proj_dir = tmp_path / "syntax_error"
        proj_dir.mkdir()

        bad_py = proj_dir / "bad.py"
        bad_py.write_text("def broken(:\n    pass")

        tracer = CallTracer(project_root=proj_dir)
        discovered = tracer.trace_file(bad_py)

        # Should handle gracefully and record error
        assert len(discovered) == 0
        # File may or may not be in visited depending on where error occurs

    def test_trace_already_visited(self, simple_project: Path):
        """Test that already visited files are not re-traced."""
        tracer = CallTracer(project_root=simple_project)

        # Trace main.py first
        tracer.trace_file(simple_project / "main.py")
        calls_after_first = len(tracer.discovered_calls)

        # Trace it again
        discovered = tracer.trace_file(simple_project / "main.py")

        # Should return empty and not add more calls
        assert len(discovered) == 0
        assert len(tracer.discovered_calls) == calls_after_first

    def test_multiple_entry_points(self, tmp_path: Path):
        """Test tracing with multiple entry points."""
        proj_dir = tmp_path / "multi_entry"
        proj_dir.mkdir()

        # Two independent entry points
        entry1 = proj_dir / "entry1.py"
        entry1.write_text("""
from shared import common

def run1():
    return common()
""")

        entry2 = proj_dir / "entry2.py"
        entry2.write_text("""
from shared import common

def run2():
    return common()
""")

        shared = proj_dir / "shared.py"
        shared.write_text("""
def common():
    return "shared"
""")

        tracer = CallTracer(project_root=proj_dir)
        all_files = tracer.trace_all([proj_dir / "entry1.py", proj_dir / "entry2.py"])

        # Should discover all three files
        assert len(all_files) == 3
        assert (proj_dir / "entry1.py").resolve() in all_files
        assert (proj_dir / "entry2.py").resolve() in all_files
        assert (proj_dir / "shared.py").resolve() in all_files
