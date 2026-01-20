# llmfiles/core/jedi_tracer.py
"""
Jedi-based call tracer for building complete call graphs from Python entry points.

This module uses Jedi's semantic analysis to trace function calls and discover
all project code that gets executed from a given entry point.
"""
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import structlog

log = structlog.get_logger(__name__)


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
    Traces function calls using Jedi to build a complete call graph.

    Starting from entry point files, discovers all project files that are
    reachable through function calls, method invocations, and imports.
    """
    project_root: Path
    call_graph: Dict[Path, Set[Path]] = field(default_factory=dict)
    visited_files: Set[Path] = field(default_factory=set)
    discovered_calls: List[CallInfo] = field(default_factory=list)
    parse_errors: List[Tuple[Path, str]] = field(default_factory=list)

    def __post_init__(self):
        self.project_root = self.project_root.resolve()
        self.call_graph = {}
        self.visited_files = set()
        self.discovered_calls = []
        self.parse_errors = []

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
        Trace all calls from a file, return discovered project files.

        Uses Jedi to analyze the file and find all references that resolve
        to definitions within the project.
        """
        try:
            import jedi
        except ImportError:
            log.error("jedi_not_installed", message="Install jedi: uv add jedi")
            return set()

        file_path = file_path.resolve()
        if file_path in self.visited_files:
            return set()

        if not file_path.suffix == ".py":
            log.debug("skipping_non_python_file", file=str(file_path))
            return set()

        self.visited_files.add(file_path)
        discovered: Set[Path] = set()

        try:
            code = file_path.read_text(encoding="utf-8")
        except Exception as e:
            log.warning("failed_to_read_file", file=str(file_path), error=str(e))
            self.parse_errors.append((file_path, str(e)))
            return set()

        try:
            script = jedi.Script(code, path=file_path, project=jedi.Project(path=self.project_root))
        except Exception as e:
            log.warning("jedi_script_creation_failed", file=str(file_path), error=str(e))
            self.parse_errors.append((file_path, str(e)))
            return set()

        # Get all names used in the file
        try:
            names = script.get_names(all_scopes=True, definitions=False, references=True)
        except Exception as e:
            log.warning("jedi_get_names_failed", file=str(file_path), error=str(e))
            self.parse_errors.append((file_path, str(e)))
            return set()

        for name in names:
            try:
                # Try to infer what this name refers to
                definitions = script.infer(name.line, name.column)

                for defn in definitions:
                    # Skip builtins and stdlib
                    if defn.in_builtin_module():
                        continue

                    module_path = defn.module_path
                    if module_path and self._is_in_project(module_path):
                        resolved_path = Path(module_path).resolve()

                        if resolved_path != file_path and resolved_path not in discovered:
                            discovered.add(resolved_path)

                            # Record the call relationship
                            call_info = CallInfo(
                                from_file=file_path,
                                from_name=name.name,
                                from_line=name.line,
                                to_file=resolved_path,
                                to_name=defn.name,
                                to_line=defn.line or 0,
                            )
                            self.discovered_calls.append(call_info)

                            log.debug(
                                "discovered_call",
                                from_file=str(file_path.relative_to(self.project_root)),
                                to_file=str(resolved_path.relative_to(self.project_root)),
                                name=name.name,
                            )

            except Exception as e:
                # Jedi can fail on certain constructs - log and continue
                log.debug("jedi_inference_failed", name=name.name, error=str(e))
                continue

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
        """Render call graph as markdown."""
        if not self.discovered_calls:
            return ""

        lines = ["## Call Graph\n"]

        # Group calls by source file
        calls_by_file: Dict[Path, List[CallInfo]] = {}
        for call in self.discovered_calls:
            if call.from_file not in calls_by_file:
                calls_by_file[call.from_file] = []
            calls_by_file[call.from_file].append(call)

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

        lines.append("\nCall relationships:")

        # Show unique file-to-file relationships
        file_relationships: Dict[Tuple[Path, Path], Set[str]] = {}
        for call in self.discovered_calls:
            key = (call.from_file, call.to_file)
            if key not in file_relationships:
                file_relationships[key] = set()
            file_relationships[key].add(f"{call.from_name}:{call.from_line} -> {call.to_name}:{call.to_line}")

        for (from_file, to_file), calls in sorted(file_relationships.items()):
            try:
                from_rel = from_file.relative_to(self.project_root)
                to_rel = to_file.relative_to(self.project_root)
            except ValueError:
                from_rel = from_file
                to_rel = to_file

            lines.append(f"\n{from_rel} -> {to_rel}")
            for call_detail in sorted(calls)[:5]:  # Limit to first 5 calls per relationship
                lines.append(f"    {call_detail}")
            if len(calls) > 5:
                lines.append(f"    ... and {len(calls) - 5} more")

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
