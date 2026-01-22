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
    names: List[str] = field(default_factory=list)  # Specific names imported (for 'from X import a, b')
    is_star: bool = False  # True for 'from X import *'


@dataclass
class ImportedSymbol:
    """Tracks an imported symbol and whether it's used."""
    name: str           # The local name (e.g., "func_a" or alias)
    module: str         # Source module (e.g., "helpers")
    line: int           # Import line number


class ImportVisitor(ast.NodeVisitor):
    """AST visitor to find all import statements in a Python file."""

    def __init__(self):
        self.imports: List[ImportInfo] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            # Get the local name (alias or first part of dotted import)
            local_name = alias.asname or alias.name.split('.')[0]
            self.imports.append(ImportInfo(
                module=alias.name,
                line=node.lineno,
                level=0,
                names=[local_name],
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        # node.module can be None for "from . import x" style imports
        module = node.module or ""
        # Check for star import
        is_star = len(node.names) == 1 and node.names[0].name == '*'
        # Get the local names being imported
        names = [alias.asname or alias.name for alias in node.names]
        self.imports.append(ImportInfo(
            module=module,
            line=node.lineno,
            level=node.level,
            names=names,
            is_star=is_star,
        ))
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


class SymbolUsageVisitor(ast.NodeVisitor):
    """Tracks imported symbols and their actual usage in code.

    This visitor performs two related tasks:
    1. Records all imported symbols (from 'import X' and 'from X import Y')
    2. Tracks all name references in the code

    The intersection gives us which imports are actually used.
    """

    def __init__(self):
        self.imported_symbols: Dict[str, ImportedSymbol] = {}
        self.referenced_names: Set[str] = set()
        self.module_imports: Dict[str, str] = {}  # alias -> module name
        self.star_import_modules: List[str] = []  # Modules with star imports

    def visit_Import(self, node: ast.Import) -> None:
        # import X, import X as Y
        for alias in node.names:
            name = alias.asname or alias.name.split('.')[0]
            self.module_imports[name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # from X import a, b, c
        module = node.module or ''
        if node.names[0].name == '*':
            # Star imports - we must follow these (can't determine usage)
            self.star_import_modules.append(module)
            return
        for alias in node.names:
            name = alias.asname or alias.name
            self.imported_symbols[name] = ImportedSymbol(
                name=name,
                module=module,
                line=node.lineno
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # Any name reference: func(), var, Type, etc.
        self.referenced_names.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # module.func() style - track the base name
        if isinstance(node.value, ast.Name):
            self.referenced_names.add(node.value.id)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Handle string annotations like "SomeType" in forward references
        if isinstance(node.value, str):
            # Could be a type annotation string - add to references
            self.referenced_names.add(node.value)
        self.generic_visit(node)

    def get_used_imports(self) -> List[ImportedSymbol]:
        """Return only imports that are actually referenced."""
        return [
            sym for sym in self.imported_symbols.values()
            if sym.name in self.referenced_names
        ]

    def get_used_module_imports(self) -> List[str]:
        """Return module names for 'import X' that are used."""
        return [
            module for alias, module in self.module_imports.items()
            if alias in self.referenced_names
        ]

    def get_used_modules(self) -> Set[str]:
        """Return set of all module names that are actually used."""
        used_modules: Set[str] = set()

        # Add modules from used 'from X import Y' statements
        for sym in self.get_used_imports():
            used_modules.add(sym.module)

        # Add modules from used 'import X' statements
        for module in self.get_used_module_imports():
            used_modules.add(module)

        # Star imports must always be followed
        for module in self.star_import_modules:
            used_modules.add(module)

        return used_modules


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

    Args:
        project_root: The root directory of the project
        filter_unused: When True, only follow imports for symbols that are actually
            used in the code. This can significantly reduce the number of files
            traced by skipping imports that are never referenced.
    """
    project_root: Path
    filter_unused: bool = False
    call_graph: Dict[Path, Set[Path]] = field(default_factory=dict)
    visited_files: Set[Path] = field(default_factory=set)
    discovered_calls: List[CallInfo] = field(default_factory=list)
    parse_errors: List[Tuple[Path, str]] = field(default_factory=list)
    skipped_imports: List[Tuple[Path, str, int]] = field(default_factory=list)  # (file, module, line)
    _source_paths: Optional[List[Path]] = field(default=None, repr=False)

    def __post_init__(self):
        self.project_root = self.project_root.resolve()
        self.call_graph = {}
        self.visited_files = set()
        self.discovered_calls = []
        self.parse_errors = []
        self.skipped_imports = []
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

    def _filter_unused_imports(
        self,
        code: str,
        imports: List[ImportInfo],
        file_path: Path,
        rel_path: Path,
    ) -> List[ImportInfo]:
        """Filter imports to only include those whose symbols are actually used.

        Args:
            code: The source code of the file
            imports: List of all imports found in the file
            file_path: Absolute path to the file
            rel_path: Relative path for logging

        Returns:
            Filtered list of ImportInfo objects for imports that are used
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # If we can't parse, return all imports (conservative)
            return imports

        # Analyze symbol usage
        usage_visitor = SymbolUsageVisitor()
        usage_visitor.visit(tree)

        # Filter imports: keep only those whose symbols are used
        filtered_imports = []
        for import_info in imports:
            # Star imports must always be followed
            if import_info.is_star:
                filtered_imports.append(import_info)
                continue

            # Check if any of the imported names are used
            # For 'import X' style, check if X is in referenced names
            # For 'from X import Y' style, check if any name is used
            should_include = False

            if import_info.level > 0:
                # Relative imports - check if module or any name is used
                # We need to be conservative here since the module name is relative
                for name in import_info.names:
                    if name in usage_visitor.referenced_names:
                        should_include = True
                        break
            else:
                # Absolute imports
                if import_info.module in usage_visitor.module_imports.values():
                    # 'import X' style - check if X is used
                    for alias, mod in usage_visitor.module_imports.items():
                        if mod == import_info.module and alias in usage_visitor.referenced_names:
                            should_include = True
                            break
                else:
                    # 'from X import Y' style - check if any name is used
                    for name in import_info.names:
                        if name in usage_visitor.referenced_names:
                            should_include = True
                            break

            if should_include:
                filtered_imports.append(import_info)
            else:
                # Track skipped imports for debugging/reporting
                self.skipped_imports.append((file_path, import_info.module, import_info.line))
                log.debug(
                    "skipping_unused_import",
                    file=str(rel_path),
                    module=import_info.module,
                    line=import_info.line,
                    names=import_info.names,
                )

        original_count = len(imports)
        filtered_count = len(filtered_imports)
        if original_count != filtered_count:
            log.info(
                "filtered_unused_imports",
                file=str(rel_path),
                original=original_count,
                kept=filtered_count,
                removed=original_count - filtered_count,
            )

        return filtered_imports

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

        # Apply symbol filtering if enabled
        if self.filter_unused and imports:
            imports = self._filter_unused_imports(code, imports, file_path, rel_path)

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
