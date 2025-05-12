# llmfiles/cli.py
"""
command line interface for llmfiles.
handles user input, configuration loading (cli > profile > file > defaults),
logging setup, and orchestrates the prompt generation process.
"""

import sys
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, cast
from dataclasses import fields as dataclass_fields, MISSING

import click
import tiktoken  # type: ignore
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
import structlog
import logging as stdlib_logging  # for log level constants

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
from .config_file import get_merged_config_defaults, CONFIG_TO_PROMPTCONFIG_MAP
from .logging_setup import configure_logging
from .discovery import discover_paths
from .processing import process_file_content, PYYAML_AVAILABLE
from .git_utils import get_diff, get_diff_branches, get_log_branches, check_is_git_repo
from .templating import TemplateRenderer, build_template_context
from .output import write_to_stdout, write_to_file, copy_to_clipboard
from .exceptions import SmartPromptBuilderError, TokenizerError, ConfigError

log = structlog.get_logger(__name__)


# --- PromptGenerator Class (as previously defined) ---
class PromptGenerator:
    """orchestrates the prompt generation pipeline."""

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
        self.log.info("step_1_discover_paths_start", base_dir=str(self.config.base_dir))
        paths = list(discover_paths(self.config))
        self.log.info("step_1_discover_paths_complete", found_paths_count=len(paths))
        return paths

    def _process_file_contents(
        self, paths_to_process: List[Path], progress_bar: Progress
    ) -> None:
        self.log.info(
            "step_2_process_file_contents_start", num_paths=len(paths_to_process)
        )
        task_id = progress_bar.add_task(
            "processing files...", total=len(paths_to_process)
        )
        processed_files_count, skipped_files_count = 0, 0
        for file_path in paths_to_process:
            processed_result = process_file_content(file_path, self.config)
            if processed_result:
                formatted_content, raw_content_for_template, modification_time = (
                    processed_result
                )
                if not self.config.base_dir:
                    raise ConfigError("base_dir not set before processing.")
                relative_path_obj = (
                    file_path.relative_to(self.config.base_dir)
                    if file_path.is_relative_to(self.config.base_dir)
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
                if modification_time is not None:
                    file_entry["mod_time"] = modification_time
                self.file_data.append(file_entry)
                processed_files_count += 1
            else:
                skipped_files_count += 1
            progress_bar.update(task_id, advance=1)
        progress_bar.update(
            task_id,
            description=f"processed {processed_files_count} files (skipped {skipped_files_count}).",
        )
        self.log.info(
            "step_2_process_file_contents_complete",
            included=processed_files_count,
            skipped=skipped_files_count,
        )

    def _sort_file_data(self) -> None:
        self.log.info(
            "step_3_sorting_files_start",
            num_files=len(self.file_data),
            method=self.config.sort_method.value,
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
                self.log.warning(
                    "file_sort_failed", error=str(e), note="proceeding unsorted."
                )
        self.log.info("step_3_sorting_files_complete")

    def _fetch_git_information(self) -> None:
        self.log.info("step_4_fetch_git_info_start")
        if not self.config.base_dir or not check_is_git_repo(self.config.base_dir):
            if any(
                [
                    self.config.diff,
                    self.config.git_diff_branch,
                    self.config.git_log_branch,
                ]
            ):
                self.log.warning(
                    "git_ops_skipped_not_repo_or_git_unavailable",
                    path=str(self.config.base_dir),
                )
            return
        try:
            if self.config.diff:
                self.log.debug("fetching_staged_git_diff")
                self.git_diff_data = get_diff(self.config.base_dir)
            if self.config.git_diff_branch:
                b1, b2 = self.config.git_diff_branch
                self.log.debug("fetching_git_branch_diff", base=b1, compare=b2)
                self.git_diff_branches_data = get_diff_branches(
                    self.config.base_dir, b1, b2
                )
            if self.config.git_log_branch:
                b1, b2 = self.config.git_log_branch
                self.log.debug("fetching_git_branch_log", base=b1, compare=b2)
                self.git_log_branches_data = get_log_branches(
                    self.config.base_dir, b1, b2
                )
        except SmartPromptBuilderError as e:
            self.log.error("git_operation_failed_known_error", error=str(e))
        self.log.info("step_4_fetch_git_info_complete")

    def _render_final_prompt(self) -> None:
        self.log.info("step_5_render_template_start")
        template_context = build_template_context(
            self.config,
            self.file_data,
            self.git_diff_data,
            self.git_diff_branches_data,
            self.git_log_branches_data,
        )
        renderer = TemplateRenderer(self.config)
        self.rendered_prompt = renderer.render(template_context)
        self.log.info("step_5_render_template_complete")

    def _calculate_prompt_tokens(self) -> None:
        if (
            self.config.show_tokens_format or self.config.console_show_token_count
        ) and self.rendered_prompt:
            self.log.info("step_6_count_tokens_start", encoding=self.config.encoding)
            try:
                encoder = tiktoken.get_encoding(self.config.encoding)
                self.token_count = len(
                    encoder.encode(self.rendered_prompt, disallowed_special=())
                )
            except Exception as e:
                raise TokenizerError(
                    f"token calculation failed for encoding '{self.config.encoding}': {e}"
                )
            self.log.info("step_6_count_tokens_complete", count=self.token_count)

    def generate(self) -> str:
        app_log_level = stdlib_logging.getLogger("llmfiles").getEffectiveLevel()
        is_progress_disabled = (
            app_log_level > stdlib_logging.INFO or not sys.stderr.isatty()
        )
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            transient=False,
            disable=is_progress_disabled,
        ) as progress_bar:
            discover_task = progress_bar.add_task("discovering paths...", total=1)
            discovered_file_paths = self._discover_paths()
            progress_bar.update(
                discover_task,
                completed=1,
                description=f"found {len(discovered_file_paths)} potential paths.",
            )
            if discovered_file_paths:
                self._process_file_contents(discovered_file_paths, progress_bar)
            else:
                progress_bar.update(
                    progress_bar.add_task("processing files...", total=1),
                    completed=1,
                    description="no files to process.",
                )
            sort_task = progress_bar.add_task("sorting files...", total=1)
            self._sort_file_data()
            progress_bar.update(
                sort_task,
                completed=1,
                description=f"sorted {len(self.file_data)} file entries.",
            )
            if any(
                [
                    self.config.diff,
                    self.config.git_diff_branch,
                    self.config.git_log_branch,
                ]
            ):
                git_task = progress_bar.add_task("fetching git info...", total=1)
                self._fetch_git_information()
                progress_bar.update(
                    git_task,
                    completed=1,
                    description="git info fetched (if applicable).",
                )
            render_task = progress_bar.add_task("rendering prompt...", total=1)
            self._render_final_prompt()
            progress_bar.update(
                render_task, completed=1, description="prompt rendered."
            )
            if self.config.show_tokens_format or self.config.console_show_token_count:
                token_task = progress_bar.add_task("counting tokens...", total=1)
                self._calculate_prompt_tokens()
                token_count_display = (
                    str(self.token_count) if self.token_count is not None else "n/a"
                )
                progress_bar.update(
                    token_task,
                    completed=1,
                    description=f"tokens: {token_count_display}.",
                )
        if not self.rendered_prompt:
            raise SmartPromptBuilderError("prompt generation resulted in no content.")
        return self.rendered_prompt


# --- console summary output utility ---
def _print_console_summary_output(config: PromptConfig, generator: PromptGenerator):
    log.debug("console_summary_output_check")
    if config.console_show_summary:
        click.secho("--- execution summary ---", fg="cyan", err=True)
        click.echo(f"files processed for prompt: {len(generator.file_data)}", err=True)
    if config.console_show_tree and generator.file_data:
        tree_context = build_template_context(
            config, generator.file_data, None, None, None
        )
        if tree_context.get("source_tree"):
            click.secho(
                "\n--- project structure (console preview) ---", fg="cyan", err=True
            )
            click.echo(tree_context["source_tree"], err=True)
    if config.console_show_token_count:
        tc_val = generator.token_count
        token_display = f"{tc_val:,}" if tc_val is not None else "not calculated or n/a"
        click.secho(
            f"\nestimated token count ({config.encoding}): {token_display}",
            fg="yellow",
            err=True,
        )


# --- main execution flow for the default command ---
def _execute_main_prompt_generation_flow(effective_config: PromptConfig):
    log.info("main_flow_started", config_type=effective_config.__class__.__name__)
    try:
        prompt_generator_instance = PromptGenerator(effective_config)
        final_prompt_str = prompt_generator_instance.generate()
        log.info("prompt_generation_pipeline_successful")
        output_to_write: str
        is_json_output_mode = effective_config.output_format == OutputFormat.JSON
        if is_json_output_mode:
            log.debug("formatting_output_as_json_structure")
            json_payload: Dict[str, Any] = {
                "prompt_content": final_prompt_str,
                "metadata": {
                    "base_directory": str(effective_config.base_dir)
                    if effective_config.base_dir
                    else None,
                    "files_included_count": len(prompt_generator_instance.file_data),
                    "output_format_requested": effective_config.output_format.value,
                    "template_source_identifier": (
                        str(effective_config.template_path)
                        if effective_config.template_path
                        else (
                            effective_config.preset_template.value
                            if effective_config.preset_template
                            else "default_for_format"
                        )
                    ),
                },
            }
            if prompt_generator_instance.token_count is not None:
                json_payload["token_information"] = {
                    "count": prompt_generator_instance.token_count,
                    "encoding_used": effective_config.encoding,
                }
            try:
                output_to_write = json.dumps(json_payload, indent=2) + "\n"
            except TypeError as e:
                log.error("json_serialization_failed", error=str(e), exc_info=True)
                click.echo(
                    "error: failed to create json. falling back to raw prompt.",
                    err=True,
                )
                output_to_write = final_prompt_str
                is_json_output_mode = False
        else:
            output_to_write = final_prompt_str
        output_destination_was_used = False
        if effective_config.output_file:
            write_to_file(effective_config.output_file, output_to_write)
            click.echo(f"info: output to: {effective_config.output_file}", err=True)
            output_destination_was_used = True
        clipboard_copy_was_ok = False
        if effective_config.clipboard:
            content_for_clipboard = (
                final_prompt_str if is_json_output_mode else output_to_write
            )
            if copy_to_clipboard(content_for_clipboard.strip()):
                clipboard_copy_was_ok = True
            output_destination_was_used = True
        if not output_destination_was_used or (
            effective_config.clipboard and not clipboard_copy_was_ok
        ):
            if effective_config.clipboard and not clipboard_copy_was_ok:
                click.echo(
                    "info: clipboard copy failed. outputting to stdout.",
                    file=sys.stderr,
                )
            log.info("writing_final_prompt_to_stdout")
            write_to_stdout(output_to_write)
        if (
            effective_config.show_tokens_format
            and prompt_generator_instance.token_count is not None
        ):
            fmt = effective_config.show_tokens_format
            count_str = (
                f"{prompt_generator_instance.token_count:,}"
                if fmt == TokenCountFormat.HUMAN
                else str(prompt_generator_instance.token_count)
            )
            click.echo(
                f"token count (main output, enc: '{effective_config.encoding}'): {count_str}",
                err=True,
            )
        elif effective_config.show_tokens_format:
            log.warning("show_tokens_requested_but_not_available")
            click.echo(
                f"token count (main output, enc: '{effective_config.encoding}'): n/a.",
                err=True,
            )
        _print_console_summary_output(effective_config, prompt_generator_instance)
    except SmartPromptBuilderError as e:
        log.error(
            "main_flow_error_known",
            error_message=str(e),
            exc_info=log.getEffectiveLevel() <= stdlib_logging.DEBUG,
        )
        click.echo(f"error: {e}", err=True)
        sys.exit(1)  # type: ignore
    except Exception as e:
        log.critical(
            "main_flow_error_unexpected_critical", error_message=str(e), exc_info=True
        )
        click.echo(f"unexpected error: {e}. see logs or run with -vv.", err=True)
        sys.exit(1)


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
    type=click.Path(readable=True, path_type=Path),
    default=None,
    help="paths to include. default: '.' if not stdin.",
)
@click.option("--stdin", is_flag=True, default=False, help="read paths from stdin.")
@click.option(
    "-0",
    "--null",
    "nul_separated",
    is_flag=True,
    default=False,
    help="stdin paths are nul-separated.",
)
@click.option(
    "-i",
    "--include",
    "include_patterns",
    multiple=True,
    help="glob patterns to include.",
)
@click.option(
    "-e",
    "--exclude",
    "exclude_patterns",
    multiple=True,
    help="glob patterns to exclude.",
)
@click.option(
    "--include-priority",
    "include_priority",
    is_flag=True,
    default=False,
    help="include overrides exclude.",
)
@click.option(
    "--no-ignore",
    "no_ignore",
    is_flag=True,
    default=False,
    help="ignore .gitignore files.",
)
@click.option(
    "--hidden", is_flag=True, default=False, help="include hidden files/dirs."
)
@click.option(
    "-L",
    "--follow-symlinks",
    "follow_symlinks",
    is_flag=True,
    default=False,
    help="follow symlinks.",
)
@click.option(
    "-t",
    "--template",
    "template_path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="path to custom handlebars template.",
)
@click.option(
    "--preset",
    "preset_template_str",
    type=click.Choice([p.value for p in PresetTemplate]),
    default=None,
    help="use a built-in preset.",
)
@click.option(
    "--var",
    "user_vars_list",
    multiple=True,
    metavar="key=value",
    help="user variables for templates.",
)
@click.option(
    "-F",
    "--output-format",
    "output_format_str",
    type=click.Choice([f.value for f in OutputFormat]),
    default=None,
    help=f"fallback output format (default: {DEFAULT_OUTPUT_FORMAT.value}).",
)
@click.option(
    "-n",
    "--line-numbers",
    "line_numbers",
    is_flag=True,
    default=False,
    help="prepend line numbers.",
)
@click.option(
    "--no-codeblock",
    "no_codeblock",
    is_flag=True,
    default=False,
    help="no markdown code blocks.",
)
@click.option(
    "--absolute-paths",
    "absolute_paths",
    is_flag=True,
    default=False,
    help="use absolute file paths in context.",
)
@click.option(
    "--yaml-truncate-long-fields",
    "process_yaml_truncate_long_fields",
    is_flag=True,
    default=False,
    help="truncate long fields in yaml (needs pyyaml).",
)
@click.option(
    "--yaml-placeholder",
    default=None,
    help=f"placeholder for truncated yaml (default: '{DEFAULT_YAML_TRUNCATION_PLACEHOLDER}').",
)
@click.option(
    "--yaml-max-len",
    "yaml_truncate_content_max_len",
    type=int,
    default=None,
    help=f"max length for yaml field truncation (default: {DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN}).",
)
@click.option(
    "--sort",
    "sort_method_str",
    type=click.Choice([s.value for s in SortMethod]),
    default=None,
    help=f"sort files by method (default: {DEFAULT_SORT_METHOD.value}).",
)
@click.option(
    "--diff", "diff", is_flag=True, default=False, help="include staged git diff."
)
@click.option(
    "--git-diff-branch", nargs=2, metavar="base comp", help="git diff between branches."
)
@click.option(
    "--git-log-branch", nargs=2, metavar="base comp", help="git log between branches."
)
@click.option(
    "-c",
    "--encoding",
    default=None,
    help=f"tiktoken encoding for token count (default: {DEFAULT_ENCODING}).",
)
@click.option(
    "--show-tokens",
    "show_tokens_format_str",
    type=click.Choice([f.value for f in TokenCountFormat]),
    default=None,
    help="show token count (main output) on stderr.",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="write output to file.",
)
@click.option(
    "--clipboard", is_flag=True, default=False, help="copy prompt to clipboard."
)
@click.option(
    "--console-tree/--no-console-tree",
    default=None,
    help="show/hide project tree on console.",
)
@click.option(
    "--console-summary/--no-console-summary",
    default=None,
    help="show/hide file count summary on console.",
)
@click.option(
    "--console-tokens/--no-console-tokens",
    "console_show_token_count",
    default=None,
    help="show/hide token count on console.",
)
@click.option(
    "--config-profile",
    "active_config_profile_name",
    default=None,
    help="load a specific profile from config file(s).",
)
@click.option(
    "--verbose",
    "-v",
    "verbosity_level",
    count=True,
    help="verbosity: -v info, -vv debug.",
)
@click.option(
    "--force-json-logs",
    "force_json_logs_cli",
    is_flag=True,
    default=False,
    help="force json output for logs.",
)
@click.version_option(
    version=app_version, package_name="llmfiles", prog_name="llmfiles"
)
@click.pass_context
def main_cli_group(
    ctx: click.Context, **cli_params_from_click: Any
):  # cli_params_from_click collects all options by their dest name
    """llmfiles: build llm prompts from codebases, git info, and templates."""

    log_level = "warning"
    if cli_params_from_click.get("verbosity_level", 0) == 1:
        log_level = "info"
    elif cli_params_from_click.get("verbosity_level", 0) >= 2:
        log_level = "debug"
    configure_logging(
        log_level_str=log_level,
        force_json_logs=cli_params_from_click.get("force_json_logs_cli", False),
    )

    log.debug(
        "cli_group_invoked",
        raw_cli_params=cli_params_from_click,
        invoked_subcommand=ctx.invoked_subcommand,
    )

    if ctx.invoked_subcommand is None:
        log.debug("default_command_flow_starting")
        try:
            file_and_profile_defaults = get_merged_config_defaults()
            profile_name = cli_params_from_click.get("active_config_profile_name")
            effective_config_source = {**file_and_profile_defaults}
            if profile_name:
                profile_cfg = file_and_profile_defaults.get("profiles", {}).get(
                    profile_name, {}
                )
                if profile_cfg:
                    log.info("applying_config_profile", profile_name=profile_name)
                    effective_config_source.update(profile_cfg)
                else:
                    log.warning("config_profile_not_found", profile_name=profile_name)

            pc_init_kwargs: Dict[str, Any] = {}
            for field_def in dataclass_fields(PromptConfig):
                if not field_def.init:
                    continue
                pc_attr_name = field_def.name

                # 1. start with hardcoded dataclass default
                if field_def.default_factory is not MISSING:
                    current_value = field_def.default_factory()
                elif field_def.default is not MISSING:
                    current_value = field_def.default
                else:
                    current_value = None  # Should have a default or default_factory
                log.debug(
                    "attr_initial_default", attr=pc_attr_name, value=current_value
                )

                # 2. override with value from (profile > file) config
                toml_key: Optional[str] = None
                for cfg_k_map, pc_k_map in CONFIG_TO_PROMPTCONFIG_MAP.items():
                    if pc_k_map == pc_attr_name:
                        toml_key = cfg_k_map
                        break

                if toml_key and toml_key in effective_config_source:
                    current_value = effective_config_source[toml_key]
                    log.debug(
                        "attr_value_from_config_file_or_profile",
                        attr=pc_attr_name,
                        value=current_value,
                        source_key=toml_key,
                    )

                # 3. override with cli value if explicitly set by user
                # cli_params_from_click keys are the 'dest' names from @click.option.
                # assuming dest names match PromptConfig field names.
                if pc_attr_name in cli_params_from_click:
                    cli_value = cli_params_from_click[pc_attr_name]
                    # use param.name (which is the dest) for get_parameter_source.
                    # this requires iterating ctx.command.params if pc_attr_name might not be the direct dest.
                    # for simplicity, assume pc_attr_name is the dest for now.
                    source_for_this_attr = ctx.get_parameter_source(pc_attr_name)

                    if (
                        source_for_this_attr == click.core.ParameterSource.COMMANDLINE
                    ):  # Corrected Enum
                        current_value = cli_value
                        log.debug(
                            "value_from_cli_explicit",
                            attr=pc_attr_name,
                            value=current_value,
                        )
                    elif (
                        cli_value is not None
                        and source_for_this_attr == click.core.ParameterSource.DEFAULT
                    ):
                        # this means click provided its own default for an option the user didn't set.
                        # if the current_value is still the hardcoded dataclass default,
                        # it implies no file/profile config set it, so click's default can apply.
                        dataclass_default_val = (
                            field_def.default_factory()
                            if field_def.default_factory is not MISSING
                            else field_def.default
                        )
                        if current_value == dataclass_default_val:
                            current_value = cli_value
                            log.debug(
                                "value_from_click_option_default",
                                attr=pc_attr_name,
                                value=current_value,
                            )

                pc_init_kwargs[pc_attr_name] = current_value

            # post-process specific args needing conversion
            cli_user_vars = cli_params_from_click.get("user_vars_list")
            if (
                ctx.get_parameter_source("user_vars_list")
                == click.core.ParameterSource.COMMANDLINE
                and cli_user_vars is not None
            ):
                uv_dict: Dict[str, str] = {}
                for v_str in cast(List[str], cli_user_vars):
                    if "=" not in v_str:
                        raise click.BadParameter(
                            f"invalid --var '{v_str}'. use k=v.", param_hint="--var"
                        )
                    k, v = v_str.split("=", 1)
                    uv_dict[k.strip()] = v
                pc_init_kwargs["user_vars"] = uv_dict
            elif pc_init_kwargs.get("user_vars") is None:
                pc_init_kwargs["user_vars"] = {}

            for enum_attr, enum_cls, enum_hard_default in [
                ("preset_template", PresetTemplate, None),
                ("output_format", OutputFormat, DEFAULT_OUTPUT_FORMAT),
                ("sort_method", SortMethod, DEFAULT_SORT_METHOD),
                ("show_tokens_format", TokenCountFormat, None),
            ]:
                val_to_convert = pc_init_kwargs.get(enum_attr)
                if isinstance(val_to_convert, str):
                    enum_member = enum_cls.from_string(val_to_convert)
                    pc_init_kwargs[enum_attr] = (
                        enum_member if enum_member else enum_hard_default
                    )
                    if enum_member is None:
                        log.warning(
                            "invalid_enum_str_using_default",
                            attr=enum_attr,
                            val=val_to_convert,
                            default=enum_hard_default,
                        )
                elif (
                    val_to_convert is None
                    and enum_hard_default is not None
                    and pc_init_kwargs.get(enum_attr) is None
                ):
                    pc_init_kwargs[enum_attr] = enum_hard_default

            current_input_paths_val = pc_init_kwargs.get("input_paths")
            if not current_input_paths_val and not pc_init_kwargs.get(
                "read_from_stdin"
            ):
                pc_init_kwargs["input_paths"] = [Path(".")]
            elif isinstance(current_input_paths_val, tuple):
                pc_init_kwargs["input_paths"] = list(current_input_paths_val)

            final_config = PromptConfig(**pc_init_kwargs)

            if final_config.process_yaml_truncate_long_fields and not PYYAML_AVAILABLE:
                click.echo(
                    "warning: yaml truncation requested but pyyaml not installed. skipping. "
                    "install with: pip install llmfiles[yaml_tools]",
                    err=True,
                )

            _execute_main_prompt_generation_flow(final_config)

        except (click.ClickException, ConfigError, SmartPromptBuilderError) as e:
            log.error(
                "cli_execution_error_known",
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=log.getEffectiveLevel() <= stdlib_logging.DEBUG,
            )  # type: ignore
            if isinstance(e, click.ClickException):
                e.show()
            else:
                click.echo(f"error: {e}", err=True)
            sys.exit(1)
        except Exception as e:
            log.critical(
                "cli_execution_error_unexpected_critical",
                error_message=str(e),
                exc_info=True,
            )
            click.echo(f"unexpected error: {e}. please report this.", err=True)
            sys.exit(1)

def main_cli_entrypoint():
    main_cli_group(prog_name="llmfiles")

if __name__ == '__main__':
    main_cli_entrypoint()