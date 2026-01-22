# llmfiles/core/pipeline.py
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.console import Console as RichConsole
import structlog
import logging as stdlib_logging

import collections
from llmfiles.config.settings import PromptConfig, ExternalDepsStrategy, OutputFormat
from llmfiles.core.discovery.walker import discover_paths, grep_files_for_content
from llmfiles.core.processing import process_file_content_to_elements
from llmfiles.exceptions import SmartPromptBuilderError
from llmfiles.structured_processing.language_parsers.python_parser import extract_python_imports
from llmfiles.core.discovery.dependency_resolver import resolve_import
from llmfiles.core.import_tracer import CallTracer

log = structlog.get_logger(__name__)


# TODO: This should be dynamically sourced, not hardcoded.
# For now, using the list from pyproject.toml is sufficient for the logic.
INSTALLED_PACKAGES = {
    "click", "pathspec", "rich", "structlog",
    "tree-sitter", "tree-sitter-language-pack"
}


class PromptGenerator:
    # orchestrates the prompt generation pipeline.
    def __init__(self, config: PromptConfig):
        self.config: PromptConfig = config
        self.log = structlog.get_logger(f"{__name__}.{self.__class__.__name__}")
        self.content_elements: List[Dict[str, Any]] = []
        self.external_dependencies: Dict[str, Set[str]] = collections.defaultdict(set)
        self.call_graph_summary: Optional[str] = None

    def _render_final_output(self) -> str:
        """Renders the collected content elements into the final markdown string."""
        if self.config.output_format == OutputFormat.COMPACT:
            return self._render_compact_output()
        else:
            return self._render_verbose_output()

    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f}TB"

    def _render_file_index(self, elements_by_file: dict, sorted_file_paths: list) -> str:
        """Render compact file index table with sizes and descriptions."""
        lines = [
            "## Files\n",
            "| File | Size | Lines | Description |",
            "|------|------|-------|-------------|"
        ]
        for file_path in sorted_file_paths:
            elements = elements_by_file[file_path]
            # Get first element for file-level info
            first_elem = elements[0]
            size = self._format_size(first_elem.get('file_size_bytes', 0))
            line_count = first_elem.get('line_count', first_elem.get('end_line', 0))
            desc = first_elem.get('description') or ''
            # Truncate long descriptions
            if len(desc) > 60:
                desc = desc[:57] + '...'
            lines.append(f"| {file_path} | {size} | {line_count} | {desc} |")
        return "\n".join(lines)

    def _render_compact_output(self) -> str:
        """Render output in compact format optimized for LLM consumption."""
        output_parts = []
        project_root_name = self.config.base_dir.name or str(self.config.base_dir)

        # 1. Header
        output_parts.append(f"# {project_root_name}\n")

        # Group elements by file path
        elements_by_file = collections.defaultdict(list)
        for el in self.content_elements:
            elements_by_file[el["file_path"]].append(el)
        sorted_file_paths = sorted(elements_by_file.keys())

        # 2. File Index Table
        if sorted_file_paths:
            output_parts.append(self._render_file_index(elements_by_file, sorted_file_paths))
            output_parts.append("")

        # 3. Code Content
        if self.content_elements:
            output_parts.append("## Code\n")
            for file_path in sorted_file_paths:
                for element in elements_by_file[file_path]:
                    line_count = element.get('line_count', element.get('end_line', 0))
                    output_parts.append(f"### {file_path} ({line_count} lines)")
                    output_parts.append(f"{element.get('llm_formatted_content')}")
                    output_parts.append("")

        # 4. Dependency Graph (at end, if --trace-calls was used)
        if self.call_graph_summary:
            output_parts.append("---\n")
            output_parts.append(self.call_graph_summary)

        return "\n".join(output_parts)

    def _render_verbose_output(self) -> str:
        """Render output in verbose/legacy format with full metadata upfront."""
        output_parts = []
        project_root_name = self.config.base_dir.name or str(self.config.base_dir)

        output_parts.append(f"project root: {project_root_name}")

        # Add call graph summary if available (from --trace-calls)
        if self.call_graph_summary:
            output_parts.append("")
            output_parts.append(self.call_graph_summary)

        # Group elements by file path to structure the output
        elements_by_file = collections.defaultdict(list)
        for el in self.content_elements:
            elements_by_file[el["file_path"]].append(el)

        sorted_file_paths = sorted(elements_by_file.keys())

        if sorted_file_paths:
            tree_lines = [f"{project_root_name}/"]
            for i, path_str in enumerate(sorted_file_paths):
                prefix = "└── " if i == len(sorted_file_paths) - 1 else "├── "
                tree_lines.append(f"{prefix}{path_str}")

            output_parts.append("\nproject structure (based on included content):\n```text")
            output_parts.append("\n".join(tree_lines))
            output_parts.append("```")

        if self.content_elements:
            output_parts.append("\ncontent elements:")
            for file_path in sorted_file_paths:
                output_parts.append("---")
                output_parts.append(f"source file: {file_path}")

                # Add external dependency metadata if requested
                if self.config.external_deps_strategy == ExternalDepsStrategy.METADATA and file_path in self.external_dependencies:
                    deps = sorted(list(self.external_dependencies[file_path]))
                    if deps:
                        output_parts.append("external dependencies:")
                        for dep in deps:
                            output_parts.append(f"  - {dep}")

                # Render each element within the file
                for element in elements_by_file[file_path]:
                    output_parts.append(f"--- (element: {element.get('qualified_name', element.get('name', 'N/A'))})")
                    output_parts.append(f"element type: {element.get('element_type', 'unknown')}")
                    if element.get('qualified_name'):
                        output_parts.append(f"qualified name: {element.get('qualified_name')}")
                    output_parts.append(f"lines: {element.get('start_line')}-{element.get('end_line')}")
                    output_parts.append(f"language hint: {element.get('language')}")
                    if element.get('docstring'):
                        output_parts.append("docstring:\n```")
                        output_parts.append(element.get('docstring'))
                        output_parts.append("```")
                    output_parts.append("content:")
                    output_parts.append(f"{element.get('llm_formatted_content')}")

            output_parts.append("---")

        return "\n".join(output_parts) + "\n"

    def _resolve_dependencies(self, seed_files: List[Path]) -> List[Path]:
        """
        Performs dependency resolution to build a complete list of files.
        """
        worklist = collections.deque(seed_files)
        processed_files = set(seed_files)

        self.log.info("starting_dependency_resolution", seed_count=len(seed_files))

        while worklist:
            current_file = worklist.popleft()

            if not current_file.suffix == ".py":
                self.log.debug("skipping_non_python_file_for_deps", file=str(current_file))
                continue

            try:
                content_bytes = current_file.read_bytes()
                imports = extract_python_imports(content_bytes)
            except Exception as e:
                self.log.warning("failed_to_extract_imports", file=str(current_file), error=str(e))
                continue

            for import_name in imports:
                status, result = resolve_import(import_name, self.config.base_dir, INSTALLED_PACKAGES)

                if status == "internal":
                    new_file_path = self.config.base_dir / result
                    if new_file_path not in processed_files:
                        self.log.debug("discovered_internal_dependency", source=str(current_file), target=str(new_file_path))
                        processed_files.add(new_file_path)
                        worklist.append(new_file_path)
                elif status in ["external", "stdlib"]:
                    rel_path_str = str(current_file.relative_to(self.config.base_dir))
                    self.external_dependencies[rel_path_str].add(result)

        return sorted(list(processed_files))


    def generate(self) -> Tuple[str, List[Dict[str, Any]]]:
        # runs the full pipeline and returns the final prompt and list of included files with metadata.
        app_log_level = stdlib_logging.getLogger("llmfiles").getEffectiveLevel()
        progress_disabled = app_log_level > stdlib_logging.INFO or not sys.stderr.isatty()
        stderr_console = RichConsole(file=sys.stderr)

        with Progress(
            SpinnerColumn(), TextColumn("[bold blue]{task.description}"), BarColumn(),
            transient=True, disable=progress_disabled, console=stderr_console
        ) as progress:

            discover_task = progress.add_task("discovering seed files...", total=None)

            # Determine the seeding strategy
            if self.config.grep_content_pattern:
                # Grep mode: seeds are determined by content search
                seed_files = list(grep_files_for_content(self.config))
            else:
                # Standard mode: seeds are from paths and patterns
                seed_files = list(discover_paths(self.config))
            progress.update(discover_task, completed=True, description=f"discovered {len(seed_files)} seed files.")

            # Conditional dependency resolution
            if self.config.trace_calls:
                # Jedi-based semantic call tracing (Python only)
                trace_task = progress.add_task("tracing calls with Jedi...", total=None)
                tracer = CallTracer(project_root=self.config.base_dir)
                paths_to_process = tracer.trace_all(seed_files)
                self.call_graph_summary = tracer.get_call_graph_summary()
                progress.update(trace_task, completed=True, description=f"traced {len(paths_to_process)} files.")
            elif self.config.recursive:
                resolve_task = progress.add_task("resolving dependencies...", total=None)
                paths_to_process = self._resolve_dependencies(seed_files)
                progress.update(resolve_task, completed=True, description=f"total files to include: {len(paths_to_process)}")
            else:
                paths_to_process = seed_files

            if paths_to_process:
                processing_task = progress.add_task("processing content...", total=len(paths_to_process))
                for file_path in paths_to_process:
                    elements_from_file = process_file_content_to_elements(file_path, self.config)
                    self.content_elements.extend(elements_from_file)
                    progress.update(processing_task, advance=1, description=f"processing {file_path.name}")

        if not self.content_elements:
            return "", []

        final_output = self._render_final_output()

        # Build file info dict with path and size
        file_info_map = {}
        for el in self.content_elements:
            file_path = el["file_path"]
            if file_path not in file_info_map:
                file_info_map[file_path] = {
                    "path": file_path,
                    "size_bytes": el.get("file_size_bytes", 0)
                }

        unique_files_info = sorted(file_info_map.values(), key=lambda x: x["path"])

        return final_output, unique_files_info
