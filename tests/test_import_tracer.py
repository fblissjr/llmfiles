"""Tests for AST-based import tracing."""
import pytest
from pathlib import Path
from llmfiles.core.import_tracer import (
    CallTracer,
    CallInfo,
    ImportInfo,
    ImportedSymbol,
    SymbolUsageVisitor,
    find_imports_ast,
    resolve_relative_import,
    resolve_import_to_path,
)


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
        assert "## Import Dependency Graph" in summary
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


class TestSrcLayoutProject:
    """Tests for src-layout projects (like llm-dit-experiments)."""

    @pytest.fixture
    def src_layout_project(self, tmp_path: Path):
        """Creates a src-layout project: tests/ imports from src/mypackage/."""
        proj_dir = tmp_path / "src_layout_proj"
        proj_dir.mkdir()

        # Create src/mypackage structure
        src_dir = proj_dir / "src" / "mypackage"
        src_dir.mkdir(parents=True)

        (src_dir / "__init__.py").write_text("""
from .core import process
from .utils import helper
""")

        (src_dir / "core.py").write_text("""
def process(data):
    return data.upper()
""")

        (src_dir / "utils.py").write_text("""
def helper():
    return "help"
""")

        # Create tests/ structure
        tests_dir = proj_dir / "tests"
        tests_dir.mkdir()

        (tests_dir / "__init__.py").write_text("")

        (tests_dir / "test_core.py").write_text("""
from mypackage import process
from mypackage.utils import helper

def test_process():
    assert process("hello") == "HELLO"

def test_helper():
    assert helper() == "help"
""")

        return proj_dir

    def test_src_layout_discovery(self, src_layout_project: Path):
        """Test that imports from src/ are discovered from tests/."""
        tracer = CallTracer(project_root=src_layout_project)
        all_files = tracer.trace_all([src_layout_project / "tests" / "test_core.py"])

        # Should discover test file and src files
        assert len(all_files) >= 3  # At least test_core.py, __init__.py, core.py or utils.py

        # Specifically check src files were found
        src_init = (src_layout_project / "src" / "mypackage" / "__init__.py").resolve()
        src_core = (src_layout_project / "src" / "mypackage" / "core.py").resolve()
        src_utils = (src_layout_project / "src" / "mypackage" / "utils.py").resolve()

        assert src_init in all_files or src_core in all_files or src_utils in all_files

    def test_src_path_detection(self, src_layout_project: Path):
        """Test that src/ is automatically added to source paths."""
        tracer = CallTracer(project_root=src_layout_project)
        source_paths = tracer._get_source_paths()

        # src/ directory should be in source paths
        assert any(p.name == "src" for p in source_paths)


class TestRelativeImports:
    """Tests for relative import resolution."""

    @pytest.fixture
    def package_with_relative_imports(self, tmp_path: Path):
        """Creates a package using relative imports."""
        proj_dir = tmp_path / "rel_imports_proj"
        proj_dir.mkdir()

        pkg_dir = proj_dir / "mypackage"
        pkg_dir.mkdir()

        # __init__.py uses relative imports
        (pkg_dir / "__init__.py").write_text("""
from .protocol import Backend
from .impl import DefaultBackend
""")

        (pkg_dir / "protocol.py").write_text("""
class Backend:
    def run(self):
        pass
""")

        (pkg_dir / "impl.py").write_text("""
from .protocol import Backend

class DefaultBackend(Backend):
    def run(self):
        return "default"
""")

        # Entry point at project root
        (proj_dir / "main.py").write_text("""
from mypackage import Backend, DefaultBackend

def main():
    backend = DefaultBackend()
    return backend.run()
""")

        return proj_dir

    def test_relative_imports_resolved(self, package_with_relative_imports: Path):
        """Test that relative imports are properly resolved."""
        tracer = CallTracer(project_root=package_with_relative_imports)
        all_files = tracer.trace_all([package_with_relative_imports / "main.py"])

        # Should discover all files including those via relative imports
        pkg_dir = package_with_relative_imports / "mypackage"
        protocol = (pkg_dir / "protocol.py").resolve()
        impl = (pkg_dir / "impl.py").resolve()

        assert protocol in all_files
        assert impl in all_files

    def test_find_imports_includes_relative(self):
        """Test that find_imports_ast captures relative imports."""
        code = """
from .protocol import Backend
from ..utils import helper
import os
from mypackage.core import process
"""
        imports = find_imports_ast(code)

        # Should find 4 imports total
        assert len(imports) == 4

        # Check relative imports are captured with correct level
        protocol_import = next(i for i in imports if i.module == "protocol")
        assert protocol_import.level == 1

        utils_import = next(i for i in imports if i.module == "utils")
        assert utils_import.level == 2

        # Absolute imports have level 0
        os_import = next(i for i in imports if i.module == "os")
        assert os_import.level == 0

    def test_resolve_relative_import_single_dot(self, tmp_path: Path):
        """Test resolving single-dot relative imports."""
        proj_dir = tmp_path / "proj"
        pkg_dir = proj_dir / "mypackage"
        pkg_dir.mkdir(parents=True)

        init_file = pkg_dir / "__init__.py"
        init_file.write_text("")

        import_info = ImportInfo(module="protocol", line=1, level=1)
        result = resolve_relative_import(import_info, init_file, proj_dir)

        assert result == "mypackage.protocol"

    def test_resolve_relative_import_double_dot(self, tmp_path: Path):
        """Test resolving double-dot relative imports."""
        proj_dir = tmp_path / "proj"
        sub_pkg = proj_dir / "mypackage" / "sub"
        sub_pkg.mkdir(parents=True)

        module_file = sub_pkg / "module.py"
        module_file.write_text("")

        import_info = ImportInfo(module="utils", line=1, level=2)
        result = resolve_relative_import(import_info, module_file, proj_dir)

        assert result == "mypackage.utils"


class TestLazyImports:
    """Tests for lazy imports inside functions."""

    def test_lazy_imports_discovered(self, tmp_path: Path):
        """Test that imports inside functions are discovered."""
        proj_dir = tmp_path / "lazy_proj"
        proj_dir.mkdir()

        (proj_dir / "main.py").write_text("""
def load_heavy_module():
    from heavy import HeavyClass
    return HeavyClass()

def another_function():
    import utils
    return utils.do_something()
""")

        (proj_dir / "heavy.py").write_text("""
class HeavyClass:
    pass
""")

        (proj_dir / "utils.py").write_text("""
def do_something():
    return 42
""")

        tracer = CallTracer(project_root=proj_dir)
        all_files = tracer.trace_all([proj_dir / "main.py"])

        # Should discover both lazy-imported files
        heavy = (proj_dir / "heavy.py").resolve()
        utils = (proj_dir / "utils.py").resolve()

        assert heavy in all_files
        assert utils in all_files


class TestSymbolUsageVisitor:
    """Tests for the SymbolUsageVisitor class."""

    def test_tracks_import_from_symbols(self):
        """Test that from X import Y symbols are tracked."""
        from llmfiles.core.import_tracer import SymbolUsageVisitor
        import ast

        code = """
from helpers import func_a, func_b, func_c
from models import User, Admin

result = func_a()  # Used
user = User()      # Used
"""
        tree = ast.parse(code)
        visitor = SymbolUsageVisitor()
        visitor.visit(tree)

        used_imports = visitor.get_used_imports()
        used_names = {sym.name for sym in used_imports}

        assert "func_a" in used_names
        assert "User" in used_names
        assert "func_b" not in used_names  # Not used
        assert "func_c" not in used_names  # Not used
        assert "Admin" not in used_names   # Not used

    def test_tracks_import_module(self):
        """Test that import X style imports are tracked."""
        from llmfiles.core.import_tracer import SymbolUsageVisitor
        import ast

        code = """
import os
import sys
import json

path = os.path.join("a", "b")  # os is used
"""
        tree = ast.parse(code)
        visitor = SymbolUsageVisitor()
        visitor.visit(tree)

        used_modules = visitor.get_used_module_imports()

        assert "os" in used_modules
        assert "sys" not in used_modules  # Not used
        assert "json" not in used_modules  # Not used

    def test_tracks_attribute_access(self):
        """Test that module.attr style usage is tracked."""
        from llmfiles.core.import_tracer import SymbolUsageVisitor
        import ast

        code = """
import config

value = config.DEBUG
other = config.get_setting("key")
"""
        tree = ast.parse(code)
        visitor = SymbolUsageVisitor()
        visitor.visit(tree)

        # config should be referenced
        assert "config" in visitor.referenced_names

    def test_handles_star_imports(self):
        """Test that star imports are flagged for mandatory inclusion."""
        from llmfiles.core.import_tracer import SymbolUsageVisitor
        import ast

        code = """
from constants import *
"""
        tree = ast.parse(code)
        visitor = SymbolUsageVisitor()
        visitor.visit(tree)

        # Star imports should be in the list
        assert "constants" in visitor.star_import_modules

    def test_handles_aliases(self):
        """Test that aliased imports are properly tracked."""
        from llmfiles.core.import_tracer import SymbolUsageVisitor
        import ast

        code = """
from helpers import long_function_name as func
import some_module as sm

result = func()
data = sm.get_data()
"""
        tree = ast.parse(code)
        visitor = SymbolUsageVisitor()
        visitor.visit(tree)

        # The alias names should be referenced
        assert "func" in visitor.referenced_names
        assert "sm" in visitor.referenced_names

    def test_handles_type_annotations(self):
        """Test that type annotations are tracked as usage."""
        from llmfiles.core.import_tracer import SymbolUsageVisitor
        import ast

        code = """
from typing import List, Optional
from models import User

def get_users() -> List[User]:
    pass
"""
        tree = ast.parse(code)
        visitor = SymbolUsageVisitor()
        visitor.visit(tree)

        # Types used in annotations should be referenced
        assert "List" in visitor.referenced_names
        assert "User" in visitor.referenced_names


class TestCallTracerWithFiltering:
    """Tests for CallTracer with filter_unused=True."""

    @pytest.fixture
    def unused_imports_project(self, tmp_path: Path):
        """Creates a project with some unused imports."""
        proj_dir = tmp_path / "unused_proj"
        proj_dir.mkdir()

        # main.py imports multiple modules but only uses some
        (proj_dir / "main.py").write_text("""
from helpers import used_func, unused_func
from models import User, Admin, Guest
import config
import unused_config

def main():
    result = used_func()
    user = User("test")
    setting = config.DEBUG
    return result, user, setting
""")

        # Create all the imported modules
        (proj_dir / "helpers.py").write_text("""
def used_func():
    return "used"

def unused_func():
    return "unused"
""")

        (proj_dir / "models.py").write_text("""
class User:
    def __init__(self, name):
        self.name = name

class Admin(User):
    pass

class Guest:
    pass
""")

        (proj_dir / "config.py").write_text("""
DEBUG = True
""")

        (proj_dir / "unused_config.py").write_text("""
SETTINGS = {}
""")

        return proj_dir

    def test_filter_excludes_unused_imports(self, unused_imports_project: Path):
        """Test that unused imports are filtered out."""
        tracer = CallTracer(project_root=unused_imports_project, filter_unused=True)
        all_files = tracer.trace_all([unused_imports_project / "main.py"])

        main_path = (unused_imports_project / "main.py").resolve()
        helpers_path = (unused_imports_project / "helpers.py").resolve()
        models_path = (unused_imports_project / "models.py").resolve()
        config_path = (unused_imports_project / "config.py").resolve()
        unused_config_path = (unused_imports_project / "unused_config.py").resolve()

        # Should include main and used modules
        assert main_path in all_files
        assert helpers_path in all_files  # used_func is used
        assert models_path in all_files   # User is used
        assert config_path in all_files   # config.DEBUG is used

        # Should NOT include unused_config (never referenced)
        assert unused_config_path not in all_files

    def test_no_filter_includes_all_imports(self, unused_imports_project: Path):
        """Test that without filtering, all imports are included."""
        tracer = CallTracer(project_root=unused_imports_project, filter_unused=False)
        all_files = tracer.trace_all([unused_imports_project / "main.py"])

        unused_config_path = (unused_imports_project / "unused_config.py").resolve()

        # Without filtering, unused_config should be included
        assert unused_config_path in all_files

    def test_filter_tracks_skipped_imports(self, unused_imports_project: Path):
        """Test that skipped imports are tracked for reporting."""
        tracer = CallTracer(project_root=unused_imports_project, filter_unused=True)
        tracer.trace_all([unused_imports_project / "main.py"])

        # Should have skipped the unused_config import
        skipped_modules = [mod for _, mod, _ in tracer.skipped_imports]
        assert "unused_config" in skipped_modules

    def test_star_imports_always_followed(self, tmp_path: Path):
        """Test that star imports are always followed even with filtering."""
        proj_dir = tmp_path / "star_proj"
        proj_dir.mkdir()

        (proj_dir / "main.py").write_text("""
from constants import *

# We don't explicitly reference anything from constants
# but star import should still be followed
def main():
    return 42
""")

        (proj_dir / "constants.py").write_text("""
DEBUG = True
VERBOSE = False
""")

        tracer = CallTracer(project_root=proj_dir, filter_unused=True)
        all_files = tracer.trace_all([proj_dir / "main.py"])

        constants_path = (proj_dir / "constants.py").resolve()
        # Star imports must be followed even with filtering
        assert constants_path in all_files

    def test_comparison_filter_vs_no_filter(self, unused_imports_project: Path):
        """Test that filtering reduces file count compared to no filtering."""
        tracer_filtered = CallTracer(
            project_root=unused_imports_project,
            filter_unused=True
        )
        files_filtered = tracer_filtered.trace_all([unused_imports_project / "main.py"])

        tracer_unfiltered = CallTracer(
            project_root=unused_imports_project,
            filter_unused=False
        )
        files_unfiltered = tracer_unfiltered.trace_all([unused_imports_project / "main.py"])

        # Filtered should have fewer or equal files
        assert len(files_filtered) <= len(files_unfiltered)
        # In this specific case, should be fewer
        assert len(files_filtered) < len(files_unfiltered)
