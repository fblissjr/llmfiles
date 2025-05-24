# llmfiles/core/pipeline.py
"""
Core pipeline for generating prompts. Includes the PromptGenerator class
and helpers for stages like pattern loading, discovery, processing, and rendering.
"""
import sys
import json
from pathlib import Path
from typing import List, Optional, Dict, Any # cast, Tuple not directly used here

import click # For secho in _load_patterns_from_file, consider moving to cli part
import tiktoken # type: ignore
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.console import Console as RichConsole
import structlog
import logging as stdlib_logging

from llmfiles.config.settings import PromptConfig, SortMethod, OutputFormat, TokenCountFormat, ChunkStrategy # Enums for type hints
from llmfiles.core.discovery import discover_paths
from llmfiles.core.processing import process_file_content_to_elements # Will be new func in processing.py
from llmfiles.core.git_utils import get_diff, get_diff_branches, get_log_branches, check_is_git_repo
from llmfiles.core.templating import TemplateRenderer, build_template_context
# exceptions might be raised by imported functions
from llmfiles.exceptions import SmartPromptBuilderError, TokenizerError, ConfigError 

log = structlog.get_logger(__name__)

def _load_patterns_from_file(file_path: Path) -> List[str]:
    """Loads patterns from a file: one per line, ignores comments (#) and empty lines."""
    patterns: List[str] = []
    try:
        with file_path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    patterns.append(stripped)
        log.info("patterns_loaded_from_file", path=str(file_path), count=len(patterns))
    except FileNotFoundError:
        log.error("pattern_file_not_found", path=str(file_path), issue="File does not exist.")
        click.secho(f"Warning: Pattern file not found: {file_path}", fg="yellow", err=True)
    except IOError as e:
        log.error("pattern_file_read_error", path=str(file_path), error_message=str(e))
        click.secho(f"Warning: Error reading pattern file {file_path}: {e}", fg="yellow", err=True)
    return patterns

class PromptGenerator:
    """Orchestrates the prompt generation pipeline."""
    def __init__(self, config: PromptConfig):
        self.config: PromptConfig = config
        self.log = structlog.get_logger(f"{__name__}.{self.__class__.__name__}")
        
        # self.file_data renamed to self.content_elements
        # Each element is a dict, representing either a whole file or a chunk (e.g. function/class)
        self.content_elements: List[Dict[str, Any]] = []
        
        self.git_diff_data: Optional[str] = None
        self.git_diff_branches_data: Optional[str] = None
        self.git_log_branches_data: Optional[str] = None
        self.rendered_prompt: Optional[str] = None
        self.token_count: Optional[int] = None

    def _prepare_patterns(self):
        """Loads patterns from files and extends config.include/exclude_patterns."""
        if self.config.include_from_files:
            includes_from_file_data = []
            for f_path in self.config.include_from_files:
                includes_from_file_data.extend(_load_patterns_from_file(f_path))
            # Ensure include_patterns is a list before extending
            if not isinstance(self.config.include_patterns, list): self.config.include_patterns = []
            self.config.include_patterns.extend(includes_from_file_data)
            log.info("extended_include_patterns_from_files", count=len(includes_from_file_data), total=len(self.config.include_patterns))

        if self.config.exclude_from_files:
            excludes_from_file_data = []
            for f_path in self.config.exclude_from_files:
                excludes_from_file_data.extend(_load_patterns_from_file(f_path))
            if not isinstance(self.config.exclude_patterns, list): self.config.exclude_patterns = []
            self.config.exclude_patterns.extend(excludes_from_file_data)
            log.info("extended_exclude_patterns_from_files", count=len(excludes_from_file_data), total=len(self.config.exclude_patterns))


    def _discover_paths(self) -> List[Path]:
        self.log.info("discovering_paths", base_dir=str(self.config.base_dir))
        # Patterns should be prepared before discovery
        paths = list(discover_paths(self.config)) # discover_paths uses config.include/exclude_patterns
        self.log.info("paths_discovered_for_processing", count=len(paths))
        return paths

    def _process_content_elements(self, paths_to_process: List[Path], progress_bar: Progress) -> None:
        """Processes files into content elements (chunks or whole files) based on strategy."""
        self.log.info("processing_content_elements", num_paths=len(paths_to_process), strategy=self.config.chunk_strategy.value)
        task_id = progress_bar.add_task("processing content...", total=len(paths_to_process))
        processed_count, skipped_count = 0, 0

        for file_path in paths_to_process:
            # process_file_content_to_elements will handle chunking strategy
            elements_from_file = process_file_content_to_elements(file_path, self.config)
            if elements_from_file:
                self.content_elements.extend(elements_from_file)
                processed_count += 1
            else:
                skipped_count += 1
            progress_bar.update(task_id, advance=1)
        
        progress_bar.update(task_id, description=f"processed {processed_count} files into {len(self.content_elements)} elements (skipped {skipped_count} files).")
        self.log.info("content_element_processing_complete", num_elements=len(self.content_elements), included_files=processed_count, skipped_files=skipped_count)

    def _sort_content_elements(self) -> None:
        """Sorts the collected content elements based on config settings."""
        sort_method_val = self.config.sort_method.value if self.config.sort_method else "not_specified"
        self.log.info("sorting_content_elements", num_elements=len(self.content_elements), method=sort_method_val)
        
        key_function: Optional[Any] = None
        should_reverse = False
        sort_method = self.config.sort_method

        # Sorting key primarily uses 'relative_path' (of the original file) and 'mod_time'.
        # If chunks are from the same file, their order should ideally be preserved or based on start_line.
        # For simplicity now, sorting is file-level. Chunk order is by extraction.
        if sort_method == SortMethod.NAME_ASC:
            key_function = lambda x: (x.get("file_path", ""), x.get("start_line", 0))
        elif sort_method == SortMethod.NAME_DESC:
            key_function = lambda x: (x.get("file_path", ""), x.get("start_line", 0))
            should_reverse = True
        elif sort_method == SortMethod.DATE_ASC: # File modification time
            key_function = lambda x: (x.get("mod_time", float("inf")), x.get("file_path", ""), x.get("start_line", 0))
        elif sort_method == SortMethod.DATE_DESC:
            key_function = lambda x: (x.get("mod_time", float("-inf")), x.get("file_path", ""), x.get("start_line", 0))
            should_reverse = True
        
        if key_function:
            try:
                self.content_elements.sort(key=key_function, reverse=should_reverse)
            except Exception as e:
                self.log.warning("content_elements_sort_failed", error=str(e), exc_info=True)
        self.log.info("content_elements_sorting_complete")


    def _fetch_git_information(self) -> None:
        self.log.info("fetching_git_information")
        if not self.config.base_dir or not check_is_git_repo(self.config.base_dir):
            if any([self.config.diff, self.config.git_diff_branch, self.config.git_log_branch]):
                self.log.warning("git_operations_skipped_not_a_git_repo", path=str(self.config.base_dir))
            return
        try:
            if self.config.diff: self.git_diff_data = get_diff(self.config.base_dir)
            if self.config.git_diff_branch:
                b1, b2 = self.config.git_diff_branch
                self.git_diff_branches_data = get_diff_branches(self.config.base_dir, b1, b2)
            if self.config.git_log_branch:
                b1, b2 = self.config.git_log_branch
                self.git_log_branches_data = get_log_branches(self.config.base_dir, b1, b2)
        except SmartPromptBuilderError as e:
            self.log.error("git_operation_failed_in_pipeline", error=str(e))
        self.log.info("git_information_fetched_successfully_or_skipped")

    def _render_final_prompt(self) -> None:
        self.log.info("rendering_final_prompt_template")
        # Pass content_elements (which are chunks or whole files) to build_template_context
        context = build_template_context(
            self.config, self.content_elements, 
            self.git_diff_data, self.git_diff_branches_data, self.git_log_branches_data
        )
        renderer = TemplateRenderer(self.config)
        self.rendered_prompt = renderer.render(context)
        self.log.info("prompt_template_rendering_complete")

    def _calculate_prompt_tokens(self) -> None:
        if (self.config.show_tokens_format or self.config.console_show_token_count) and self.rendered_prompt:
            self.log.info("calculating_final_prompt_tokens", encoding=self.config.encoding)
            try:
                encoder = tiktoken.get_encoding(self.config.encoding)
                self.token_count = len(encoder.encode(self.rendered_prompt, disallowed_special=()))
            except Exception as e:
                raise TokenizerError(f"Token calculation failed for encoding '{self.config.encoding}': {e}")
            self.log.info("token_calculation_complete_for_prompt", count=self.token_count)

    def generate(self) -> str:
        """Generates the prompt through the defined pipeline stages."""
        self._prepare_patterns() # Load patterns from files first

        app_log_level = stdlib_logging.getLogger("llmfiles").getEffectiveLevel()
        progress_disabled = app_log_level > stdlib_logging.INFO or not sys.stderr.isatty()
        stderr_console = RichConsole(file=sys.stderr)

        with Progress(SpinnerColumn(), TextColumn("[bold blue]{task.description}"), BarColumn(),
                      TextColumn("[progress.percentage]{task.percentage:>3.0f}pct"), 
                      transient=True, disable=progress_disabled, console=stderr_console) as progress:
            
            discover_task_id = progress.add_task("discovering paths...", total=1)
            paths_found = self._discover_paths()
            progress.update(discover_task_id, completed=1, description=f"found {len(paths_found)} files/dirs for processing.")

            if paths_found:
                self._process_content_elements(paths_found, progress) # This adds its own task
            else:
                no_elements_task_id = progress.add_task("processing content...", total=1)
                progress.update(no_elements_task_id, completed=1, description="no content to process.")
            
            sort_task_id = progress.add_task("sorting content...", total=1)
            self._sort_content_elements()
            progress.update(sort_task_id, completed=1, description=f"sorted {len(self.content_elements)} elements.")

            if any([self.config.diff, self.config.git_diff_branch, self.config.git_log_branch]):
                git_task_id = progress.add_task("fetching git info...", total=1)
                self._fetch_git_information()
                progress.update(git_task_id, completed=1, description="git info processed.")
            
            render_task_id = progress.add_task("rendering prompt...", total=1)
            self._render_final_prompt()
            progress.update(render_task_id, completed=1, description="prompt rendered.")

            if self.config.show_tokens_format or self.config.console_show_token_count:
                token_task_id = progress.add_task("counting tokens...", total=1)
                self._calculate_prompt_tokens()
                token_display = str(self.token_count) if self.token_count is not None else "N/A"
                progress.update(token_task_id, completed=1, description=f"tokens: {token_display}.")

        if not self.rendered_prompt:
            raise SmartPromptBuilderError("Prompt generation pipeline resulted in no content.")
        return self.rendered_prompt