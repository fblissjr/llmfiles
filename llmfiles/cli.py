# llmfiles/cli.py
# Full content of cli.py with the secho fix applied, and previous RichConsole fix.

import sys
import json
import toml
from pathlib import Path
from typing import List, Optional, Dict, Any, cast, Tuple
from dataclasses import fields as dataclass_fields, MISSING, asdict
from enum import Enum

import click
import tiktoken  # type: ignore
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.console import Console as RichConsole
import structlog
import logging as stdlib_logging

from . import __version__ as app_version
from .config import (
    PromptConfig,
    SortMethod,
    OutputFormat,
    TokenCountFormat,
    PresetTemplate,
    DEFAULT_YAML_TRUNCATION_PLACEHOLDER,
    DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN,
    DEFAULT_CONSOLE_SHOW_TREE,
    DEFAULT_CONSOLE_SHOW_SUMMARY,
    DEFAULT_CONSOLE_SHOW_TOKEN_COUNT,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_SORT_METHOD,
    DEFAULT_ENCODING,
)
from .config_file import (
    get_merged_config_defaults,
    CONFIG_TO_PROMPTCONFIG_MAP,
    PROJECT_CONFIG_FILENAMES,
)
from .logging_setup import configure_logging
from .discovery import discover_paths
from .processing import process_file_content, PYYAML_AVAILABLE
from .git_utils import get_diff, get_diff_branches, get_log_branches, check_is_git_repo
from .templating import TemplateRenderer, build_template_context
from .output import write_to_stdout, write_to_file, copy_to_clipboard
from .exceptions import SmartPromptBuilderError, TokenizerError, ConfigError

log = structlog.get_logger(__name__)


class PromptGenerator:
    """Orchestrates the prompt generation pipeline."""

    def __init__(self, config: PromptConfig):
        self.config: PromptConfig = config
        self.log = structlog.get_logger(self.__class__.__name__)
        self.file_data: List[Dict[str, Any]] = []
        self.git_diff_data: Optional[str] = None
        self.git_diff_branches_data: Optional[str] = None
        self.git_log_branches_data: Optional[str] = None
        self.rendered_prompt: Optional[str] = None
        self.token_count: Optional[int] = None

    def _discover_paths(self) -> List[Path]:
        self.log.info("discovering_paths", base_dir=str(self.config.base_dir))
        paths = list(discover_paths(self.config))
        self.log.info("paths_discovered", count=len(paths))
        return paths

    def _process_file_contents(
        self, paths_to_process: List[Path], progress_bar: Progress
    ) -> None:
        self.log.info("processing_file_contents", num_paths=len(paths_to_process))
        task_id = progress_bar.add_task(
            "processing files...", total=len(paths_to_process)
        )
        processed_files_count, skipped_files_count = 0, 0
        for file_path in paths_to_process:
            processed_result = process_file_content(file_path, self.config)
            if processed_result:
                formatted_content, raw_content_for_template, mod_time = processed_result
                relative_path_obj = (
                    file_path.relative_to(self.config.base_dir)
                    if self.config.base_dir
                    and file_path.is_relative_to(self.config.base_dir)
                    else Path(file_path.name)
                )

                file_entry: Dict[str, Any] = {
                    "path": str(file_path)
                    if self.config.absolute_paths
                    else str(relative_path_obj),
                    "relative_path": str(relative_path_obj),
                    "content": formatted_content,
                    "raw_content": raw_content_for_template,
                    "extension": file_path.suffix[1:].lower()
                    if file_path.suffix
                    else "",
                }
                if mod_time is not None:
                    file_entry["mod_time"] = mod_time
                self.file_data.append(file_entry)
                processed_files_count += 1
            else:
                skipped_files_count += 1
            progress_bar.update(task_id, advance=1)
        progress_bar.update(
            task_id,
            description=f"processed {processed_files_count} (skipped {skipped_files_count}) files.",
        )
        self.log.info(
            "file_processing_complete",
            included=processed_files_count,
            skipped=skipped_files_count,
        )

    def _sort_file_data(self) -> None:
        sort_method_val = (
            self.config.sort_method.value
            if self.config.sort_method
            else "not_specified"
        )
        self.log.info(
            "sorting_files", num_files=len(self.file_data), method=sort_method_val
        )
        key_function: Optional[Any] = None
        should_reverse = False
        sort_method = self.config.sort_method
        if sort_method == SortMethod.NAME_ASC:
            key_function = lambda x: x["relative_path"]
        elif sort_method == SortMethod.NAME_DESC:
            key_function, should_reverse = lambda x: x["relative_path"], True
        elif sort_method == SortMethod.DATE_ASC:
            key_function = lambda x: x.get("mod_time", float("inf"))
        elif sort_method == SortMethod.DATE_DESC:
            key_function, should_reverse = (
                lambda x: x.get("mod_time", float("-inf")),
                True,
            )
        if key_function:
            try:
                self.file_data.sort(key=key_function, reverse=should_reverse)
            except Exception as e:
                self.log.warning("file_sort_failed", error=str(e))
        self.log.info("file_sorting_complete")

    def _fetch_git_information(self) -> None:
        self.log.info("fetching_git_information")
        if not self.config.base_dir or not check_is_git_repo(self.config.base_dir):
            if any(
                [
                    self.config.diff,
                    self.config.git_diff_branch,
                    self.config.git_log_branch,
                ]
            ):
                self.log.warning(
                    "git_ops_skipped_not_repo", path=str(self.config.base_dir)
                )
            return
        try:
            if self.config.diff:
                self.git_diff_data = get_diff(self.config.base_dir)
            if self.config.git_diff_branch:
                b1, b2 = self.config.git_diff_branch
                self.git_diff_branches_data = get_diff_branches(
                    self.config.base_dir, b1, b2
                )
            if self.config.git_log_branch:
                b1, b2 = self.config.git_log_branch
                self.git_log_branches_data = get_log_branches(
                    self.config.base_dir, b1, b2
                )
        except SmartPromptBuilderError as e:
            self.log.error("git_operation_failed", error=str(e))
        self.log.info("git_information_fetched")

    def _render_final_prompt(self) -> None:
        self.log.info("rendering_final_prompt")
        context = build_template_context(
            self.config,
            self.file_data,
            self.git_diff_data,
            self.git_diff_branches_data,
            self.git_log_branches_data,
        )
        renderer = TemplateRenderer(self.config)
        self.rendered_prompt = renderer.render(context)
        self.log.info("prompt_rendering_complete")

    def _calculate_prompt_tokens(self) -> None:
        if (
            self.config.show_tokens_format or self.config.console_show_token_count
        ) and self.rendered_prompt:
            self.log.info("calculating_prompt_tokens", encoding=self.config.encoding)
            try:
                encoder = tiktoken.get_encoding(self.config.encoding)
                self.token_count = len(
                    encoder.encode(self.rendered_prompt, disallowed_special=())
                )
            except Exception as e:
                raise TokenizerError(
                    f"Token calculation failed for '{self.config.encoding}': {e}"
                )
            self.log.info("token_calculation_complete", count=self.token_count)

    def generate(self) -> str:
        """Generates the prompt through a pipeline of discovery, processing, and rendering."""
        app_log_level = stdlib_logging.getLogger("llmfiles").getEffectiveLevel()
        progress_disabled = (
            app_log_level > stdlib_logging.INFO or not sys.stderr.isatty()
        )

        stderr_console = RichConsole(file=sys.stderr)

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}pct"),
            transient=True,
            disable=progress_disabled,
            console=stderr_console,
        ) as progress:
            discover_task = progress.add_task("discovering paths...", total=1)
            paths = self._discover_paths()
            progress.update(
                discover_task,
                completed=1,
                description=f"found {len(paths)} potential paths.",
            )

            if paths:
                self._process_file_contents(paths, progress)
            else:
                no_files_task = progress.add_task("processing files...", total=1)
                progress.update(
                    no_files_task, completed=1, description="no files to process."
                )

            sort_task = progress.add_task("sorting files...", total=1)
            self._sort_file_data()
            progress.update(
                sort_task,
                completed=1,
                description=f"sorted {len(self.file_data)} entries.",
            )

            if any(
                [
                    self.config.diff,
                    self.config.git_diff_branch,
                    self.config.git_log_branch,
                ]
            ):
                git_task = progress.add_task("fetching git info...", total=1)
                self._fetch_git_information()
                progress.update(git_task, completed=1, description="git info fetched.")

            render_task = progress.add_task("rendering prompt...", total=1)
            self._render_final_prompt()
            progress.update(render_task, completed=1, description="prompt rendered.")

            if self.config.show_tokens_format or self.config.console_show_token_count:
                token_task = progress.add_task("counting tokens...", total=1)
                self._calculate_prompt_tokens()
                token_display = (
                    str(self.token_count) if self.token_count is not None else "n/a"
                )
                progress.update(
                    token_task, completed=1, description=f"tokens: {token_display}."
                )

        if not self.rendered_prompt:
            raise SmartPromptBuilderError("Prompt generation resulted in no content.")
        return self.rendered_prompt


def _print_console_summary_output(config: PromptConfig, generator: PromptGenerator):
    """Prints summary information (tree, counts) to the console (stderr)."""
    if config.console_show_summary:
        click.secho("--- execution summary ---", fg="cyan", err=True)
        click.echo(f"files processed for prompt: {len(generator.file_data)}", err=True)
    if config.console_show_tree and generator.file_data:
        tree_ctx = build_template_context(config, generator.file_data, None, None, None)
        if tree_ctx.get("source_tree"):
            click.secho(
                "\n--- project structure (console preview) ---", fg="cyan", err=True
            )
            click.echo(tree_ctx["source_tree"], err=True)
    if config.console_show_token_count:
        tc_val = generator.token_count
        token_display = f"{tc_val:,}" if tc_val is not None else "n/a"
        click.secho(
            f"\nestimated token count ({config.encoding}): {token_display}",
            fg="yellow",
            err=True,
        )


def _execute_main_prompt_generation_flow(effective_config: PromptConfig):
    """Main execution flow: generate prompt and handle output."""
    log.info(
        "prompt_generation_flow_started",
        config_class=effective_config.__class__.__name__,
    )
    try:
        generator = PromptGenerator(effective_config)
        final_prompt = generator.generate()
        log.info("prompt_generation_successful")

        output_to_write = final_prompt
        is_json_mode = effective_config.output_format == OutputFormat.JSON

        if is_json_mode:
            output_fmt_val = (
                effective_config.output_format.value
                if effective_config.output_format
                else "unknown"
            )
            tmpl_src_id = "default_for_format"
            if effective_config.template_path:
                tmpl_src_id = str(effective_config.template_path)
            elif effective_config.preset_template:
                tmpl_src_id = effective_config.preset_template.value

            payload: Dict[str, Any] = {
                "prompt_content": final_prompt,
                "metadata": {
                    "base_directory": str(effective_config.base_dir),
                    "files_included_count": len(generator.file_data),
                    "output_format_requested": output_fmt_val,
                    "template_source_identifier": tmpl_src_id,
                },
            }
            if generator.token_count is not None:
                payload["token_information"] = {
                    "count": generator.token_count,
                    "encoding_used": effective_config.encoding,
                }
            try:
                output_to_write = json.dumps(payload, indent=2) + "\n"
            except TypeError as e:
                log.error("json_serialization_failed", error=str(e))
                click.echo(
                    "error: Failed to create JSON. Falling back to raw prompt.",
                    err=True,
                )
                is_json_mode = False

        output_done = False
        if effective_config.output_file:
            write_to_file(effective_config.output_file, output_to_write)
            click.echo(f"info: Output to: {effective_config.output_file}", err=True)
            output_done = True

        copied_ok = False
        if effective_config.clipboard:
            content_for_clip = final_prompt if is_json_mode else output_to_write
            if copy_to_clipboard(content_for_clip.strip()):
                copied_ok = True
            output_done = True

        if not output_done or (effective_config.clipboard and not copied_ok):
            if effective_config.clipboard and not copied_ok:
                click.echo(
                    "info: Clipboard copy failed. Outputting to stdout.", err=True
                )
            log.info("writing_prompt_to_stdout")
            write_to_stdout(output_to_write)

        if effective_config.show_tokens_format and generator.token_count is not None:
            fmt = effective_config.show_tokens_format
            count_display = (
                f"{generator.token_count:,}"
                if fmt == TokenCountFormat.HUMAN
                else str(generator.token_count)
            )
            click.echo(
                f"token count (enc: '{effective_config.encoding}'): {count_display}",
                err=True,
            )
        elif effective_config.show_tokens_format:
            click.echo(
                f"token count (enc: '{effective_config.encoding}'): n/a", err=True
            )

        _print_console_summary_output(effective_config, generator)

    except SmartPromptBuilderError as e:
        log.error(
            "prompt_builder_error",
            message=str(e),
            is_debug=(log.getEffectiveLevel() <= stdlib_logging.DEBUG),
        )
        click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)
    except Exception as e:
        log.critical("cli_unexpected_critical_error", message=str(e), exc_info=True)
        click.secho(
            f"Unexpected critical error: {e}. Please report this.", fg="red", err=True
        )  # Corrected to secho
        sys.exit(1)


def _load_patterns_from_file(file_path: Path) -> List[str]:
    """Loads patterns from a file: one per line, ignores comments (#) and empty lines."""
    patterns = []
    try:
        with file_path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    patterns.append(stripped)
        log.info("patterns_loaded_from_file", path=str(file_path), count=len(patterns))
    except FileNotFoundError:
        log.error("pattern_file_not_found", path=str(file_path))
        click.secho(
            f"Warning: Pattern file not found: {file_path}", fg="yellow", err=True
        )
    except IOError as e:
        log.error("pattern_file_read_error", path=str(file_path), error=str(e))
        click.secho(
            f"Warning: Error reading pattern file {file_path}: {e}",
            fg="yellow",
            err=True,
        )
    return patterns


def _save_options_to_profile(config_to_save: PromptConfig, profile_name_to_save: str):
    """Saves the current configuration to a profile in a project-local TOML file."""
    target_toml_path = Path.cwd() / ".llmfiles.toml"
    if not target_toml_path.exists():
        alt_path = Path.cwd() / "llmfiles.toml"
        if alt_path.exists():
            target_toml_path = alt_path

    log.info(
        "saving_config_to_profile",
        profile=profile_name_to_save,
        path=str(target_toml_path),
    )

    attrs_to_skip_saving = {
        "base_dir",
        "resolved_input_paths",
        "save_profile_name",
        "read_from_stdin",
        "nul_separated",
    }
    pc_attr_to_toml_key_map = {v: k for k, v in CONFIG_TO_PROMPTCONFIG_MAP.items()}
    profile_data_to_save: Dict[str, Any] = {}
    config_dict = asdict(config_to_save)

    for pc_attr, value in config_dict.items():
        if pc_attr in attrs_to_skip_saving:
            continue
        toml_key = pc_attr_to_toml_key_map.get(pc_attr)
        if not toml_key:
            log.debug("skip_saving_unmapped_attr_to_toml", attr=pc_attr)
            continue

        field_def = next(
            (f for f in dataclass_fields(PromptConfig) if f.name == pc_attr), None
        )
        if field_def:
            default_val = (
                field_def.default_factory()
                if field_def.default_factory is not MISSING
                else field_def.default
            )
            is_actually_default = value == default_val
            always_save_if_present_in_map = (
                "include_patterns",
                "exclude_patterns",
                "include_from_files",
                "exclude_from_files",
                "input_paths",
                "user_vars",
            )
            if is_actually_default and pc_attr not in always_save_if_present_in_map:
                if isinstance(value, bool):
                    log.debug(
                        "skip_saving_default_boolean_value",
                        attr=pc_attr,
                        value=value,
                        default=default_val,
                    )
                    continue
                else:
                    log.debug(
                        "skip_saving_default_non_boolean_value",
                        attr=pc_attr,
                        value=value,
                    )
                    continue

        if isinstance(value, Path):
            profile_data_to_save[toml_key] = str(value)
        elif isinstance(value, list) and all(isinstance(item, Path) for item in value):
            profile_data_to_save[toml_key] = [str(item) for item in value]
        elif isinstance(value, Enum):
            profile_data_to_save[toml_key] = value.value
        elif (
            isinstance(value, (list, dict))
            and not value
            and pc_attr in always_save_if_present_in_map
        ):
            profile_data_to_save[toml_key] = value
        elif value is not None:
            profile_data_to_save[toml_key] = value

    if not profile_data_to_save:
        log.info(
            "no_options_to_save_after_filtering_defaults", profile=profile_name_to_save
        )
        click.echo(
            f"No options differing from defaults (or needing explicit save) for profile '{profile_name_to_save}'. Nothing saved.",
            err=True,
        )
        return

    try:
        existing_toml_data: Dict[str, Any] = {}
        if target_toml_path.exists():
            try:
                existing_toml_data = toml.load(target_toml_path)
            except toml.TomlDecodeError as e:
                log.error(
                    "failed_to_load_existing_toml_for_save",
                    path=str(target_toml_path),
                    error=str(e),
                )
                click.secho(
                    f"Error: Could not read existing TOML file at {target_toml_path} to save profile. Aborting save.",
                    fg="red",
                    err=True,
                )
                return

        is_default_profile_save = profile_name_to_save.upper() == "DEFAULT"

        if is_default_profile_save:
            profiles_backup = existing_toml_data.pop("profiles", None)
            existing_toml_data.update(profile_data_to_save)
            if profiles_backup is not None:
                existing_toml_data["profiles"] = profiles_backup
        else:
            existing_toml_data.setdefault("profiles", {})
            existing_toml_data["profiles"][profile_name_to_save] = profile_data_to_save

        with target_toml_path.open("w", encoding="utf-8") as f:
            toml.dump(existing_toml_data, f)
        click.echo(
            f"Configuration saved to profile '{profile_name_to_save}' in '{target_toml_path.name}'.",
            err=True,
        )
    except Exception as e:
        log.error(
            "failed_to_write_profile_to_toml",
            profile=profile_name_to_save,
            error=str(e),
            exc_info=True,
        )
        click.secho(
            f"Error saving profile '{profile_name_to_save}': {e}", fg="red", err=True
        )


@click.group(
    context_settings=dict(help_option_names=["-h", "--help"]),
    invoke_without_command=True,
)
@click.option(
    "-in",
    "-I",
    "--input-path",
    "input_paths",
    multiple=True,
    type=click.Path(path_type=Path),
    default=None,
    help="Paths to include. Default: current directory '.' if not reading from stdin.",
)
@click.option(
    "--stdin",
    "read_from_stdin",
    is_flag=True,
    default=False,
    help="Read paths from stdin.",
)
@click.option(
    "-0",
    "--null",
    "nul_separated",
    is_flag=True,
    default=False,
    help="Input paths from stdin are NUL-separated.",
)
@click.option(
    "-i",
    "--include",
    "include_patterns",
    multiple=True,
    help="Glob patterns for files/directories to include.",
)
@click.option(
    "-e",
    "--exclude",
    "exclude_patterns",
    multiple=True,
    help="Glob patterns for files/directories to exclude.",
)
@click.option(
    "--include-from-file",
    "include_from_files",
    type=click.Path(
        exists=True, dir_okay=False, readable=True, path_type=Path, resolve_path=True
    ),
    multiple=True,
    help="File(s) with include glob patterns, one per line.",
)
@click.option(
    "--exclude-from-file",
    "exclude_from_files",
    type=click.Path(
        exists=True, dir_okay=False, readable=True, path_type=Path, resolve_path=True
    ),
    multiple=True,
    help="File(s) with exclude glob patterns, one per line.",
)
@click.option(
    "--include-priority",
    "include_priority",
    is_flag=True,
    default=False,
    help="Include patterns override exclude patterns.",
)
@click.option(
    "--no-ignore",
    "no_ignore",
    is_flag=True,
    default=False,
    help="Disable .gitignore file processing.",
)
@click.option(
    "--hidden",
    "hidden",
    is_flag=True,
    default=False,
    help="Include hidden files and directories.",
)
@click.option(
    "-L",
    "--follow-symlinks",
    "follow_symlinks",
    is_flag=True,
    default=False,
    help="Follow symbolic links.",
)
@click.option(
    "-t",
    "--template",
    "template_path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="Path to a custom Handlebars template file.",
)
@click.option(
    "--preset",
    "preset_template",
    type=click.Choice([p.value for p in PresetTemplate]),
    default=None,
    help="Use a built-in preset template.",
)
@click.option(
    "--var",
    "user_vars",
    multiple=True,
    metavar="KEY=VALUE",
    help="User-defined variables for templates (e.g., --var name=project).",
)
@click.option(
    "-F",
    "--output-format",
    "output_format",
    type=click.Choice([f.value for f in OutputFormat]),
    default=None,
    help=f"Output format if no template/preset. Default: {DEFAULT_OUTPUT_FORMAT.value}.",
)
@click.option(
    "-n",
    "--line-numbers",
    "line_numbers",
    is_flag=True,
    default=False,
    help="Prepend line numbers to file content.",
)
@click.option(
    "--no-codeblock",
    "no_codeblock",
    is_flag=True,
    default=False,
    help="Omit Markdown code blocks around file content.",
)
@click.option(
    "--absolute-paths",
    "absolute_paths",
    is_flag=True,
    default=False,
    help="Use absolute paths for files in the output list (content section).",
)
@click.option(
    "--show-abs-project-path",
    "show_absolute_project_path",
    is_flag=True,
    default=False,
    help="Show full absolute path of project root in the output header.",
)
@click.option(
    "--yaml-truncate-long-fields",
    "process_yaml_truncate_long_fields",
    is_flag=True,
    default=False,
    help="Enable truncation of long fields in YAML files (requires PyYAML).",
)
@click.option(
    "--yaml-placeholder",
    "yaml_truncate_placeholder",
    default=None,
    help=f"Placeholder for truncated YAML content. Default: '{DEFAULT_YAML_TRUNCATION_PLACEHOLDER}'.",
)
@click.option(
    "--yaml-max-len",
    "yaml_truncate_content_max_len",
    type=int,
    default=None,
    help=f"Max length for YAML field truncation. Default: {DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN}.",
)
@click.option(
    "--sort",
    "sort_method",
    type=click.Choice([s.value for s in SortMethod]),
    default=None,
    help=f"Method for sorting included files. Default: {DEFAULT_SORT_METHOD.value}.",
)
@click.option(
    "--diff",
    "diff",
    is_flag=True,
    default=False,
    help="Include staged git diff in the output.",
)
@click.option(
    "--git-diff-branch",
    "git_diff_branch",
    nargs=2,
    metavar="BASE COMPARE",
    help="Git diff between two branches/commits.",
)
@click.option(
    "--git-log-branch",
    "git_log_branch",
    nargs=2,
    metavar="BASE COMPARE",
    help="Git log between two branches/commits (commits in COMPARE not in BASE).",
)
@click.option(
    "-c",
    "--encoding",
    "encoding",
    default=None,
    help=f"Tiktoken encoding for token counting. Default: {DEFAULT_ENCODING}.",
)
@click.option(
    "--show-tokens",
    "show_tokens_format",
    type=click.Choice([f.value for f in TokenCountFormat]),
    default=None,
    help="Show estimated token count (for main output) on stderr.",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Path to write the generated prompt to.",
)
@click.option(
    "--clipboard",
    "clipboard",
    is_flag=True,
    default=False,
    help="Copy the generated prompt to the clipboard.",
)
@click.option(
    "--console-tree/--no-console-tree",
    "console_show_tree",
    default=None,
    help="Show/hide project directory tree on console (stderr).",
)
@click.option(
    "--console-summary/--no-console-summary",
    "console_show_summary",
    default=None,
    help="Show/hide file count summary on console (stderr).",
)
@click.option(
    "--console-tokens/--no-console-tokens",
    "console_show_token_count",
    default=None,
    help="Show/hide token count on console (stderr).",
)
@click.option(
    "--config-profile",
    "active_config_profile_name",
    default=None,
    help="Load a specific profile from configuration file(s).",
)
@click.option(
    "--save",
    "save_profile_name",
    type=str,
    metavar="PROFILE_NAME",
    default=None,
    help="Save current options to a profile in project's .llmfiles.toml. Use 'DEFAULT' for top-level.",
)
@click.option(
    "--verbose",
    "-v",
    "verbosity_level",
    count=True,
    help="Increase verbosity: -v for info, -vv for debug.",
)
@click.option(
    "--force-json-logs",
    "force_json_logs_cli",
    is_flag=True,
    default=False,
    help="Force JSON output for logs, even if terminal is a TTY.",
)
@click.version_option(
    version=app_version, package_name="llmfiles", prog_name="llmfiles"
)
@click.pass_context
def main_cli_group(ctx: click.Context, **cli_params_from_click: Any):
    """llmfiles: Build LLM prompts from codebases, git info, and templates."""
    log_level_str = "warning"
    if cli_params_from_click.get("verbosity_level", 0) == 1:
        log_level_str = "info"
    elif cli_params_from_click.get("verbosity_level", 0) >= 2:
        log_level_str = "debug"
    configure_logging(
        log_level_str=log_level_str,
        force_json_logs=cli_params_from_click.get("force_json_logs_cli", False),
    )

    log.debug(
        "cli_invocation",
        params=cli_params_from_click,
        invoked_subcommand=ctx.invoked_subcommand,
    )

    if ctx.invoked_subcommand is not None:
        return

    try:
        raw_config_from_files = get_merged_config_defaults()
        profile_name_from_cli = cli_params_from_click.get("active_config_profile_name")
        effective_config_values: Dict[str, Any] = {}

        for toml_key, pc_attr in CONFIG_TO_PROMPTCONFIG_MAP.items():
            if toml_key in raw_config_from_files:
                effective_config_values[pc_attr] = raw_config_from_files[toml_key]

        active_profile_data: Dict[str, Any] = {}
        if profile_name_from_cli:
            active_profile_data = raw_config_from_files.get("profiles", {}).get(
                profile_name_from_cli, {}
            )
            if active_profile_data:
                log.info(
                    "applying_profile_from_file", profile_name=profile_name_from_cli
                )
                for toml_key, pc_attr in CONFIG_TO_PROMPTCONFIG_MAP.items():
                    if toml_key in active_profile_data:
                        effective_config_values[pc_attr] = active_profile_data[toml_key]
                if "vars" in active_profile_data and isinstance(
                    active_profile_data["vars"], dict
                ):
                    effective_config_values["user_vars"] = active_profile_data["vars"]
            else:
                log.warning(
                    "specified_config_profile_not_found_in_files",
                    profile_name=profile_name_from_cli,
                )

        pc_init_kwargs: Dict[str, Any] = {}
        for fd in dataclass_fields(PromptConfig):
            if not fd.init:
                continue
            val = (
                fd.default_factory()
                if fd.default_factory is not MISSING
                else fd.default
            )

            if fd.name in effective_config_values:
                cfg_val = effective_config_values[fd.name]
                if fd.name in (
                    "input_paths",
                    "include_from_files",
                    "exclude_from_files",
                ) and isinstance(cfg_val, list):
                    val = [Path(p) for p in cfg_val if isinstance(p, (str, Path))]
                elif fd.name in ("template_path", "output_file") and isinstance(
                    cfg_val, str
                ):
                    val = Path(cfg_val) if cfg_val else None
                elif fd.name == "user_vars" and isinstance(cfg_val, dict):
                    val = cfg_val
                else:
                    val = cfg_val

            cli_val = cli_params_from_click.get(fd.name)
            if (
                ctx.get_parameter_source(fd.name)
                == click.core.ParameterSource.COMMANDLINE
            ):
                if (
                    isinstance(cli_val, tuple)
                    and hasattr(fd.type, "__origin__")
                    and fd.type.__origin__ == list
                ):
                    val = list(cli_val)
                elif fd.name == "user_vars" and cli_val:
                    val = {
                        k.strip(): v
                        for k, v in (
                            s.split("=", 1)
                            for s in cast(Tuple[str, ...], cli_val)
                            if "=" in s
                        )
                    }
                else:
                    val = cli_val
            elif (
                cli_val is not None
                and ctx.get_parameter_source(fd.name)
                == click.core.ParameterSource.DEFAULT
            ):
                is_dataclass_default = (
                    fd.default_factory()
                    if fd.default_factory is not MISSING
                    else fd.default
                ) == val
                if is_dataclass_default:
                    val = cli_val

            if fd.name in (
                "preset_template",
                "output_format",
                "sort_method",
                "show_tokens_format",
            ) and isinstance(val, str):
                enum_map = {
                    "preset_template": PresetTemplate,
                    "output_format": OutputFormat,
                    "sort_method": SortMethod,
                    "show_tokens_format": TokenCountFormat,
                }
                enum_cls = enum_map.get(fd.name)
                if enum_cls:
                    enum_member = enum_cls.from_string(val)  # type: ignore
                    if enum_member:
                        val = enum_member
                    else:
                        log.warning(
                            "invalid_enum_string_reverting_to_default",
                            field=fd.name,
                            invalid_value=val,
                        )
                        val = (
                            fd.default_factory()
                            if fd.default_factory is not MISSING
                            else fd.default
                        )
            pc_init_kwargs[fd.name] = val

        for list_field in (
            "input_paths",
            "include_patterns",
            "exclude_patterns",
            "include_from_files",
            "exclude_from_files",
        ):
            if not isinstance(pc_init_kwargs.get(list_field), list):
                pc_init_kwargs[list_field] = (
                    list(pc_init_kwargs[list_field])
                    if isinstance(pc_init_kwargs.get(list_field), tuple)
                    else []
                )

        if not pc_init_kwargs.get("input_paths") and not pc_init_kwargs.get(
            "read_from_stdin"
        ):
            pc_init_kwargs["input_paths"] = [Path(".")]

        final_config = PromptConfig(**pc_init_kwargs)

        if final_config.save_profile_name:
            _save_options_to_profile(final_config, final_config.save_profile_name)
            ctx.exit(0)  # Raise Exit(0) for a clean exit after save

        # If not saving, proceed to generate prompt
        _execute_main_prompt_generation_flow(final_config)

    except click.exceptions.Exit as e:
        # Let Click handle its own Exit exceptions (e.g., from ctx.exit()).
        # This will ensure the correct exit code is used (0 in case of ctx.exit(0)).
        # You typically don't need to do anything here other than letting it propagate
        # or explicitly sys.exit(e.exit_code). For simplicity, just re-raising or passing
        # is fine as Click's top-level main() will handle it.
        # log.debug("click_exit_exception_caught", code=e.exit_code) # Optional: log if you want to see it
        raise e  # Re-raise to let Click's infrastructure handle it properly.

    except (click.ClickException, ConfigError, SmartPromptBuilderError) as e:
        # Handle known application-specific or Click usage errors
        log.error(
            "cli_execution_error",
            error_type=type(e).__name__,
            message=str(e),
            is_debug=(log.getEffectiveLevel() <= stdlib_logging.DEBUG),
        )
        if isinstance(e, click.ClickException) and not isinstance(
            e, click.exceptions.Exit
        ):  # Ensure not to double-handle Exit
            e.show()
        else:
            click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)  # Explicitly exit with 1 for these handled errors

    except Exception as e:
        # Catch truly unexpected exceptions
        log.critical("cli_unexpected_critical_error", message=str(e), exc_info=True)
        click.secho(
            f"Unexpected critical error: {e}. Please report this.", fg="red", err=True
        )
        sys.exit(1)  # Exit with 1 for unexpected errors


def main_cli_entrypoint():
    """Main entry point for the llmfiles CLI application."""
    main_cli_group(prog_name="llmfiles")

if __name__ == '__main__':
    main_cli_entrypoint()
