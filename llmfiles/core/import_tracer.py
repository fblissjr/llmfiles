# llmfiles/core/import_tracer.py
"""
AST-based import tracer for building complete dependency graphs from Python entry points.

This module uses Python's AST to parse import statements and discover all project
code that is reachable from a given entry point through imports.

Key features:
1. Pure AST parsing - no code execution, no hangs on heavy imports
2. Finds all imports including lazy imports inside functions
3. Resolves imports to project files using src-layout aware path resolution
"""
import ast
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import structlog

log = structlog.get_logger(__name__)


@dataclass
class ImportInfo:
    """Information about an import statement."""
    module: str  # The module being imported (may be empty for relative imports)
    line: int  # Line number
    level: int = 0  # Relative import level (0=absolute, 1=., 2=.., etc)


class ImportVisitor(ast.NodeVisitor):
    """AST visitor to find all import statements in a Python file."""

    def __init__(self):
        self.imports: List[ImportInfo] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(ImportInfo(module=alias.name, line=node.lineno, level=0))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        # node.module can be None for "from . import x" style imports
        module = node.module or ""
        self.imports.append(ImportInfo(module=module, line=node.lineno, level=node.level))
        self.generic_visit(node)


def find_imports_ast(code: str) -> List[ImportInfo]:
    """Find all imports in code using AST parsing.

    Returns list of ImportInfo objects.
    Works for both top-level and in-function imports.
    Handles both absolute and relative imports.
    """
    try:
        tree = ast.parse(code)
        visitor = ImportVisitor()
        visitor.visit(tree)
        return visitor.imports
    except SyntaxError:
        return []


def resolve_relative_import(
    import_info: ImportInfo,
    current_file: Path,
    project_root: Path,
) -> Optional[str]:
    """Convert a relative import to an absolute module name.

    Args:
        import_info: The import information with level and module
        current_file: Path to the file containing the import
        project_root: The project root directory

    Returns:
        Absolute module name or None if cannot resolve
    """
    if import_info.level == 0:
        return import_info.module  # Already absolute

    # Get the package path of the current file
    try:
        rel_path = current_file.relative_to(project_root)
    except ValueError:
        return None

    # Build package parts from path
    # e.g., tests/backends/__init__.py -> ["tests", "backends"]
    # e.g., tests/backends/protocol.py -> ["tests", "backends"]
    parts = list(rel_path.parent.parts)
    if current_file.name == "__init__.py":
        # __init__.py is the package itself
        pass
    # Remove number of parts equal to level - 1
    # level=1 (.protocol) stays in same package
    # level=2 (..protocol) goes up one package
    levels_up = import_info.level - 1
    if levels_up > 0:
        parts = parts[:-levels_up] if levels_up < len(parts) else []

    if not parts:
        return import_info.module

    # Combine package path with module
    if import_info.module:
        return ".".join(parts) + "." + import_info.module
    else:
        return ".".join(parts)


def resolve_import_to_path(
    module_name: str,
    project_root: Path,
    source_paths: List[Path],
) -> Optional[Path]:
    """Resolve a module name to a file path within the project.

    Args:
        module_name: The module name (e.g., 'llm_dit.pipelines.generate')
        project_root: The project root directory
        source_paths: Additional source directories (e.g., src/)

    Returns:
        Path to the module file if found within project, None otherwise
    """
    # Convert module name to path components
    parts = module_name.split(".")

    # Try to find the module in source paths and project root
    search_paths = source_paths + [project_root]

    for base_path in search_paths:
        # Try as a package (directory with __init__.py)
        candidate = base_path / Path(*parts) / "__init__.py"
        if candidate.exists():
            return candidate

        # Try as a module (file.py)
        candidate = base_path / Path(*parts[:-1]) / f"{parts[-1]}.py" if len(parts) > 1 else base_path / f"{parts[0]}.py"
        if candidate.exists():
            return candidate

        # Try as a single file
        candidate = base_path / Path(*parts).with_suffix(".py")
        if candidate.exists():
            return candidate

    return None


@dataclass
class CallInfo:
    """Represents a single call relationship."""
    from_file: Path
    from_name: str
    from_line: int
    to_file: Path
    to_name: str
    to_line: int


@dataclass
class CallTracer:
    """
    Traces imports using AST to build a complete dependency graph.

    Starting from entry point files, discovers all project files that are
    reachable through import statements (including lazy imports inside functions).

    Uses pure AST parsing which is fast and reliable - no code execution needed.
    """
    project_root: Path
    call_graph: Dict[Path, Set[Path]] = field(default_factory=dict)
    visited_files: Set[Path] = field(default_factory=set)
    discovered_calls: List[CallInfo] = field(default_factory=list)
    parse_errors: List[Tuple[Path, str]] = field(default_factory=list)
    _source_paths: Optional[List[Path]] = field(default=None, repr=False)

    def __post_init__(self):
        self.project_root = self.project_root.resolve()
        self.call_graph = {}
        self.visited_files = set()
        self.discovered_calls = []
        self.parse_errors = []
        self._source_paths = None

    def _get_source_paths(self) -> List[Path]:
        """Get list of source directories for import resolution."""
        if self._source_paths is not None:
            return self._source_paths

        # Build list of source paths for src-layout and similar patterns
        self._source_paths = []
        for subdir in ["src", "lib", "source"]:
            candidate = self.project_root / subdir
            if candidate.is_dir():
                self._source_paths.append(candidate)
                log.info("added_source_path", path=str(candidate))

        return self._source_paths

    def _is_in_project(self, module_path: Optional[Path]) -> bool:
        """Check if a path is within project boundaries.

        Excludes virtual environments, __pycache__, and other non-project directories.
        """
        if module_path is None:
            return False
        try:
            resolved = module_path.resolve()

            # Must be within project root
            if not resolved.is_relative_to(self.project_root):
                return False

            # Exclude common virtual environment and cache directories
            excluded_dirs = {
                ".venv", "venv", ".env", "env",
                "__pycache__", ".git", ".hg",
                "node_modules", ".tox", ".nox",
                "site-packages", "dist-packages",
            }

            # Check if any part of the path contains excluded directories
            for part in resolved.relative_to(self.project_root).parts:
                if part in excluded_dirs:
                    return False

            return resolved.exists()
        except (ValueError, OSError):
            return False

    def trace_file(self, file_path: Path) -> Set[Path]:
        """
        Trace all imports from a file, return discovered project files.

        Uses AST parsing to find all import statements (including lazy imports
        inside functions) and resolves them to project file paths.

        This approach is fast and reliable - no code execution needed.
        """
        file_path = file_path.resolve()
        if file_path in self.visited_files:
            return set()

        if not file_path.suffix == ".py":
            log.debug("skipping_non_python_file", file=str(file_path))
            return set()

        self.visited_files.add(file_path)
        discovered: Set[Path] = set()

        try:
            rel_path = file_path.relative_to(self.project_root)
        except ValueError:
            rel_path = file_path
        log.info("tracing_file", file=str(rel_path))

        try:
            code = file_path.read_text(encoding="utf-8")
        except Exception as e:
            log.warning("failed_to_read_file", file=str(file_path), error=str(e))
            self.parse_errors.append((file_path, str(e)))
            return set()

        # Find all imports using AST (fast, reliable, finds lazy imports too)
        imports = find_imports_ast(code)
        if not imports:
            log.debug("no_imports_found", file=str(rel_path))

        source_paths = self._get_source_paths()

        for import_info in imports:
            # Resolve relative imports to absolute module names
            if import_info.level > 0:
                module_name = resolve_relative_import(
                    import_info, file_path, self.project_root
                )
                if module_name is None:
                    log.debug(
                        "relative_import_not_resolved",
                        module=import_info.module,
                        level=import_info.level,
                        line=import_info.line,
                    )
                    continue
            else:
                module_name = import_info.module

            # Try to resolve the import to a project file
            resolved_path = resolve_import_to_path(
                module_name, self.project_root, source_paths
            )

            if resolved_path is None:
                # Could be stdlib, third-party, or unresolvable
                log.debug("import_not_resolved", module=module_name, line=import_info.line)
                continue

            # Check if it's within project bounds
            if not self._is_in_project(resolved_path):
                log.debug(
                    "import_outside_project",
                    module=module_name,
                    path=str(resolved_path),
                )
                continue

            resolved_path = resolved_path.resolve()
            log.info(
                "found_project_import",
                module=module_name,
                path=str(resolved_path.relative_to(self.project_root)),
            )

            if resolved_path != file_path and resolved_path not in discovered:
                discovered.add(resolved_path)

                # Record the import relationship
                call_info = CallInfo(
                    from_file=file_path,
                    from_name=module_name,
                    from_line=import_info.line,
                    to_file=resolved_path,
                    to_name=module_name.split(".")[-1],
                    to_line=1,  # AST doesn't give us the target line
                )
                self.discovered_calls.append(call_info)

                log.debug(
                    "discovered_import",
                    from_file=str(rel_path),
                    to_file=str(resolved_path.relative_to(self.project_root)),
                    module=module_name,
                )

        # Update call graph
        if file_path not in self.call_graph:
            self.call_graph[file_path] = set()
        self.call_graph[file_path].update(discovered)

        return discovered

    def trace_all(self, entry_points: List[Path]) -> List[Path]:
        """
        BFS from entry points, return all discovered files.

        Traces all function calls starting from the given entry points,
        building a complete list of project files that are reachable.
        """
        worklist = deque([p.resolve() for p in entry_points])
        all_files: Set[Path] = set(worklist)

        log.info("starting_call_trace", entry_points=len(entry_points))

        while worklist:
            current_file = worklist.popleft()
            discovered = self.trace_file(current_file)

            for new_file in discovered:
                if new_file not in all_files:
                    all_files.add(new_file)
                    worklist.append(new_file)

        log.info(
            "call_trace_complete",
            total_files=len(all_files),
            total_calls=len(self.discovered_calls),
        )

        return sorted(all_files)

    def get_call_graph_summary(self) -> str:
        """Render import dependency graph as markdown."""
        if not self.discovered_calls:
            return ""

        lines = ["## Import Dependency Graph\n"]

        # Group imports by source file
        imports_by_file: Dict[Path, List[CallInfo]] = {}
        for imp in self.discovered_calls:
            if imp.from_file not in imports_by_file:
                imports_by_file[imp.from_file] = []
            imports_by_file[imp.from_file].append(imp)

        # Build tree representation
        entry_files = [f for f in self.visited_files if f not in {c.to_file for c in self.discovered_calls}]

        if entry_files:
            lines.append("Entry points:")
            for entry in sorted(entry_files):
                try:
                    rel_path = entry.relative_to(self.project_root)
                    lines.append(f"  - {rel_path}")
                except ValueError:
                    lines.append(f"  - {entry}")

        lines.append("\nImport relationships:")

        # Show unique file-to-file relationships
        file_relationships: Dict[Tuple[Path, Path], Set[str]] = {}
        for imp in self.discovered_calls:
            key = (imp.from_file, imp.to_file)
            if key not in file_relationships:
                file_relationships[key] = set()
            file_relationships[key].add(f"import {imp.from_name} (line {imp.from_line})")

        for (from_file, to_file), imports in sorted(file_relationships.items()):
            try:
                from_rel = from_file.relative_to(self.project_root)
                to_rel = to_file.relative_to(self.project_root)
            except ValueError:
                from_rel = from_file
                to_rel = to_file

            lines.append(f"\n{from_rel} -> {to_rel}")
            for import_detail in sorted(imports)[:5]:  # Limit to first 5 imports per relationship
                lines.append(f"    {import_detail}")
            if len(imports) > 5:
                lines.append(f"    ... and {len(imports) - 5} more")

        # Summary
        lines.append(f"\n## Discovered Files ({len(self.visited_files)})")
        for f in sorted(self.visited_files):
            try:
                rel_path = f.relative_to(self.project_root)
                is_entry = f in entry_files
                suffix = " (entry point)" if is_entry else ""
                lines.append(f"- {rel_path}{suffix}")
            except ValueError:
                lines.append(f"- {f}")

        if self.parse_errors:
            lines.append(f"\n## Parse Errors ({len(self.parse_errors)})")
            for path, error in self.parse_errors:
                try:
                    rel_path = path.relative_to(self.project_root)
                except ValueError:
                    rel_path = path
                lines.append(f"- {rel_path}: {error}")

        return "\n".join(lines) + "\n"
