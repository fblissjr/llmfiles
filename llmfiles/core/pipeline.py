# llmfiles/core/pipeline.py
import sys
from pathlib import Path
from typing import List, Dict, Any, Tuple

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.console import Console as RichConsole
import structlog
import logging as stdlib_logging

from llmfiles.config.settings import PromptConfig
from llmfiles.core.discovery.walker import discover_paths
from llmfiles.core.processing import process_file_content_to_elements
from llmfiles.exceptions import SmartPromptBuilderError

log = structlog.get_logger(__name__)

class PromptGenerator:
    # orchestrates the prompt generation pipeline.
    def __init__(self, config: PromptConfig):
        self.config: PromptConfig = config
        self.log = structlog.get_logger(f"{__name__}.{self.__class__.__name__}")
        self.content_elements: List[Dict[str, Any]] = []

    def _render_final_output(self) -> str:
        # renders the collected content elements into the final markdown string.
        output_parts = []
        project_root_name = self.config.base_dir.name or str(self.config.base_dir)

        output_parts.append(f"project root: {project_root_name}")

        unique_file_paths = sorted(list(set(el["file_path"] for el in self.content_elements)))
        if unique_file_paths:
            tree_lines = [f"{project_root_name}/"]
            for i, path_str in enumerate(unique_file_paths):
                prefix = "└── " if i == len(unique_file_paths) - 1 else "├── "
                tree_lines.append(f"{prefix}{path_str}")

            output_parts.append("\nproject structure (based on included content):\n```text")
            output_parts.append("\n".join(tree_lines))
            output_parts.append("```")

        if self.content_elements:
            output_parts.append("\ncontent elements:")
            for element in self.content_elements:
                output_parts.append("---")
                output_parts.append(f"element type: {element.get('element_type', 'unknown')}")
                if element.get('name'):
                    output_parts.append(f"name: {element.get('name')}")
                if element.get('qualified_name'):
                    output_parts.append(f"qualified name: {element.get('qualified_name')}")
                output_parts.append(f"source file: {element.get('file_path')}")
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

    def generate(self) -> Tuple[str, List[str]]:
        # runs the full pipeline and returns the final prompt and list of included files.
        app_log_level = stdlib_logging.getLogger("llmfiles").getEffectiveLevel()
        progress_disabled = app_log_level > stdlib_logging.INFO or not sys.stderr.isatty()
        stderr_console = RichConsole(file=sys.stderr)

        with Progress(
            SpinnerColumn(), TextColumn("[bold blue]{task.description}"), BarColumn(),
            transient=True, disable=progress_disabled, console=stderr_console
        ) as progress:

            discover_task = progress.add_task("discovering paths...", total=None)
            paths_to_process = list(discover_paths(self.config))
            progress.update(discover_task, completed=True, description=f"discovered {len(paths_to_process)} files.")

            if paths_to_process:
                processing_task = progress.add_task("processing content...", total=len(paths_to_process))
                for file_path in paths_to_process:
                    elements_from_file = process_file_content_to_elements(file_path, self.config)
                    self.content_elements.extend(elements_from_file)
                    progress.update(processing_task, advance=1)

        if not self.content_elements:
            return "", []

        final_output = self._render_final_output()
        unique_files = sorted(list(set(el["file_path"] for el in self.content_elements)))

        return final_output, unique_files
