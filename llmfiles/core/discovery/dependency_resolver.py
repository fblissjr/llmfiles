# llmfiles/core/discovery/dependency_resolver.py
import sys
from pathlib import Path
from typing import Tuple, Optional, Literal, Set

import structlog

log = structlog.get_logger(__name__)

ResolutionStatus = Literal["internal", "external", "stdlib", "unresolved"]

# A basic set of standard library modules to avoid false positives.
# This is not exhaustive but covers many common cases.
STD_LIB_MODULES = set(sys.stdlib_module_names)

def resolve_import(
    import_name: str,
    project_root: Path,
    installed_packages: Set[str],
) -> Tuple[ResolutionStatus, Optional[Path | str]]:
    """
    Resolves a Python import name to a file path or categorizes it.

    This function implements a "simple path mapping" strategy. It tries to map
    a given import name (e.g., 'my_app.core.utils') to a corresponding file
    or package directory within the project root.

    Args:
        import_name: The dot-separated import string (e.g., "my_app.utils").
        project_root: The root directory of the project to search within.
        installed_packages: A set of package names installed in the environment.

    Returns:
        A tuple containing:
        - The resolution status ('internal', 'external', 'stdlib', 'unresolved').
        - The result: a Path object for 'internal' imports, the import name
          for 'external' or 'stdlib' imports, or None for 'unresolved'.
    """
    # 1. Check if it's a standard library module
    top_level_module = import_name.split('.')[0]
    if top_level_module in STD_LIB_MODULES:
        log.debug("import_resolved_as_stdlib", import_name=import_name)
        return "stdlib", import_name

    # 2. Check if it's a known installed external package
    if top_level_module in installed_packages:
        log.debug("import_resolved_as_external", import_name=import_name)
        return "external", import_name

    # 3. Attempt to resolve as an internal project file
    path_parts = import_name.split('.')

    # Check for file: /path/to/project/part1/part2.py
    potential_file_path = project_root.joinpath(*path_parts).with_suffix(".py")
    if potential_file_path.is_file():
        log.debug("import_resolved_as_internal_file", import_name=import_name, path=str(potential_file_path))
        return "internal", potential_file_path.relative_to(project_root)

    # Check for package: /path/to/project/part1/part2/__init__.py
    potential_package_path = project_root.joinpath(*path_parts, "__init__.py")
    if potential_package_path.is_file():
        log.debug("import_resolved_as_internal_package", import_name=import_name, path=str(potential_package_path))
        return "internal", potential_package_path.relative_to(project_root)

    # 4. If all else fails, mark as unresolved
    log.debug("import_unresolved", import_name=import_name)
    return "unresolved", None
