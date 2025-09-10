import pytest
from pathlib import Path
from llmfiles.structured_processing.language_parsers.python_parser import extract_python_imports
from llmfiles.core.discovery.dependency_resolver import resolve_import

# --- Tests for extract_python_imports ---

def test_extract_simple_imports():
    code = b"import os\nimport sys\n"
    imports = extract_python_imports(code)
    assert set(imports) == {"os", "sys"}

def test_extract_from_imports():
    code = b"from pathlib import Path\nfrom typing import List, Dict\n"
    imports = extract_python_imports(code)
    assert set(imports) == {"pathlib", "typing"}

def test_extract_dotted_imports():
    code = b"import my_pkg.my_module\nfrom my_pkg.utils import helper\n"
    imports = extract_python_imports(code)
    assert set(imports) == {"my_pkg.my_module", "my_pkg.utils"}

def test_extract_no_imports():
    code = b"print('Hello, world!')\n"
    imports = extract_python_imports(code)
    assert imports == []

def test_extract_imports_with_aliases():
    code = b"import numpy as np\nfrom pandas import DataFrame as DF\n"
    imports = extract_python_imports(code)
    assert set(imports) == {"numpy", "pandas"}

# --- Tests for resolve_import ---

@pytest.fixture
def mock_project(tmp_path: Path):
    """Creates a mock project structure in a temporary directory."""
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()

    # a/b/c.py
    (proj_dir / "a" / "b").mkdir(parents=True)
    (proj_dir / "a" / "b" / "c.py").touch()

    # d/e/__init__.py
    (proj_dir / "d" / "e").mkdir(parents=True)
    (proj_dir / "d" / "e" / "__init__.py").touch()

    return proj_dir

INSTALLED_PACKAGES = {"numpy", "pandas"}

def test_resolve_internal_file(mock_project: Path):
    status, result = resolve_import("a.b.c", mock_project, INSTALLED_PACKAGES)
    assert status == "internal"
    assert result == Path("a/b/c.py")

def test_resolve_internal_package(mock_project: Path):
    status, result = resolve_import("d.e", mock_project, INSTALLED_PACKAGES)
    assert status == "internal"
    assert result == Path("d/e/__init__.py")

def test_resolve_external_package(mock_project: Path):
    status, result = resolve_import("numpy", mock_project, INSTALLED_PACKAGES)
    assert status == "external"
    assert result == "numpy"

def test_resolve_stdlib_package(mock_project: Path):
    status, result = resolve_import("os", mock_project, INSTALLED_PACKAGES)
    assert status == "stdlib"
    assert result == "os"

def test_resolve_unresolved_package(mock_project: Path):
    status, result = resolve_import("scipy.linalg", mock_project, INSTALLED_PACKAGES)
    assert status == "unresolved"
    assert result is None
