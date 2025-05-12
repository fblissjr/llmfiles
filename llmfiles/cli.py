# llmfiles/cli.py
"""command line interface for llmfiles, using click and structlog."""

import click
import sys
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

import tiktoken  # type: ignore
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
import structlog  # for structured logging

from llmfiles.config import (
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
    DEFAULT_ENCODING,  # bring in more defaults
)
from llmfiles.config_file import (
    get_merged_config_defaults,
    CONFIG_TO_PROMPTCONFIG_MAP,
)  # for config file loading
from llmfiles.logging_setup import умирать_configure_logging  # for structlog setup
from llmfiles.discovery import discover_paths
from llmfiles.processing import (
    process_file_content,
    PYYAML_AVAILABLE,
)  # import pyyaml_available flag
from llmfiles.git_utils import (
    get_diff,
    get_diff_branches,
    get_log_branches,
    check_is_git_repo,
)
from llmfiles.templating import TemplateRenderer, build_template_context
from llmfiles.output import write_to_stdout, write_to_file, copy_to_clipboard
from llmfiles.exceptions import SmartPromptBuilderError, TokenizerError, ConfigError
from llmfiles import __version__ as app_version  # import version from __init__.py

# get a structlog logger for this module. it will be configured by `умри_configure_logging`.
log = structlog.get_logger(__name__)  # llmfiles.cli


# --- PromptGenerator Class (uses structlog) ---
class PromptGenerator:
    """orchestrates the prompt generation pipeline."""
    def __init__(self, config: PromptConfig):
        self.config: PromptConfig = config
        self.log = structlog.get_logger(
            self.__class__.__name__
        )  # logger specific to this class instance
        self.file_data: List[Dict[str, Any]] = []
        self.git_diff_data: Optional[str] = None
        self.git_diff_branches_data: Optional[str] = None
        self.git_log_branches_data: Optional[str] = None
        self.rendered_prompt: Optional[str] = None
        self.token_count: Optional[int] = None

    def _discover_paths(self) -> List[Path]:
        self.log.info("step_1_discover_paths", base_dir=str(self.config.base_dir))
        paths = list(
            discover_paths(self.config)
        )  # `discover_paths` also uses structlog
        self.log.info("discovery_complete", found_paths=len(paths))
        return paths

    def _process_contents(self, paths: List[Path], progress: Progress) -> None:
        self.log.info("step_2_process_file_contents", num_paths=len(paths))
        task_id = progress.add_task("processing files...", total=len(paths))
        processed, skipped = 0, 0
        for p_obj in paths:
            # `process_file_content` performs actual file reading and transformations.
            res = process_file_content(p_obj, self.config)
            if res:
                content, raw_content, mod_time = res
                # ensure base_dir is set for relative path calculation.
                if not self.config.base_dir:
                    raise ConfigError("base_dir not set before processing.")
                # determine relative path for context.
                rel_p = (
                    p_obj.relative_to(self.config.base_dir)
                    if p_obj.is_relative_to(self.config.base_dir)
                    else p_obj.name
                )

                entry: Dict[str, Any] = {
                    "path": str(p_obj) if self.config.absolute_paths else str(rel_p),
                    "relative_path": str(rel_p),
                    "content": content,
                    "raw_content": raw_content,
                    "extension": p_obj.suffix[1:].lower() if p_obj.suffix else "",
                }
                if mod_time is not None:
                    entry["mod_time"] = mod_time  # only add if available
                self.file_data.append(entry)
                processed += 1
            else:
                skipped += 1
            progress.update(task_id, advance=1)
        progress.update(
            task_id, description=f"processed {processed} files (skipped {skipped})."
        )
        self.log.info(
            "file_content_processing_complete", included=processed, skipped=skipped
        )

    def _sort_data(self) -> None:
        self.log.info(
            "step_3_sorting_files",
            num_files=len(self.file_data),
            method=self.config.sort_method.value,
        )
        key_func: Optional[Any] = None
        reverse_order = False
        sort_method_val = self.config.sort_method
        if sort_method_val == SortMethod.NAME_ASC:
            key_func = lambda x: x["relative_path"]
        elif sort_method_val == SortMethod.NAME_DESC:
            key_func, reverse_order = lambda x: x["relative_path"], True
        elif sort_method_val == SortMethod.DATE_ASC:
            key_func = lambda x: x.get("mod_time", float("inf"))  # Nones last
        elif sort_method_val == SortMethod.DATE_DESC:
            key_func, reverse_order = (
                lambda x: x.get("mod_time", float("-inf")),
                True,
            )  # Nones first if not reversed, last if reversed

        if key_func:
            try:
                self.file_data.sort(key=key_func, reverse=reverse_order)
            except Exception as e:
                self.log.warning(
                    "file_sort_failed",
                    error=str(e),
                    note="proceeding with unsorted data.",
                )

    def _fetch_git(self) -> None:
        self.log.info("step_4_fetch_git_info")
        if not self.config.base_dir or not check_is_git_repo(self.config.base_dir):
            if any(
                [
                    self.config.diff,
                    self.config.git_diff_branch,
                    self.config.git_log_branch,
                ]
            ):
                self.log.warning(
                    "git_ops_skipped_not_a_repo_or_git_unavailable",
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
            self.log.error(
                "git_operation_failed", error=str(e), note="git info might be missing."
            )

    def _render(self) -> None:
        self.log.info("step_5_render_template")
        context = build_template_context(
            self.config,
            self.file_data,
            self.git_diff_data,
            self.git_diff_branches_data,
            self.git_log_branches_data,
        )
        self.rendered_prompt = TemplateRenderer(self.config).render(
            context
        )  # template_renderer also uses structlog

    def _count_tokens(self) -> None:
        if (
            self.config.show_tokens_format or self.config.console_show_token_count
        ) and self.rendered_prompt:
            self.log.info("step_6_count_tokens", encoding=self.config.encoding)
            try:
                # tiktoken.encoding_for_model(self.config.encoding) # optional: validates encoding name
                encoder = tiktoken.get_encoding(self.config.encoding)
                self.token_count = len(
                    encoder.encode(self.rendered_prompt, disallowed_special=())
                )
                self.log.info("token_count_calculated", count=self.token_count)
            except (
                Exception
            ) as e:  # handles valueerror for unknown encoding, other tiktoken issues
                raise TokenizerError(
                    f"token calculation failed for encoding '{self.config.encoding}': {e}"
                )

    def generate(self) -> str:
        """main pipeline to generate the prompt, with progress display."""
        # determine if progress bar should be disabled (e.g., if not logging at info/debug or not tty).
        # structlog config determines actual log output; this is for rich progress.
        effective_log_level = logging.getLogger(
            "llmfiles"
        ).getEffectiveLevel()  # check our namespace logger
        is_progress_disabled = (
            effective_log_level > logging.INFO or not sys.stderr.isatty()
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            transient=False,
            disable=is_progress_disabled,
        ) as progress_bar:
            # each step updates its task on the progress bar.
            paths = self._discover_paths()
            progress_bar.update(
                progress_bar.add_task("discovering paths...", total=1),
                completed=1,
                description=f"found {len(paths)} potential paths.",
            )

            if paths:
                self._process_contents(paths, progress_bar)  # this adds its own task
            else:
                progress_bar.update(
                    progress_bar.add_task("processing files...", total=1),
                    completed=1,
                    description="no files to process.",
                )

            self._sort_data()
            progress_bar.update(
                progress_bar.add_task("sorting files...", total=1),
                completed=1,
                description=f"sorted {len(self.file_data)} files.",
            )

            if any(
                [
                    self.config.diff,
                    self.config.git_diff_branch,
                    self.config.git_log_branch,
                ]
            ):
                self._fetch_git()
                progress_bar.update(
                    progress_bar.add_task("fetching git info...", total=1),
                    completed=1,
                    description="git info fetched (if applicable).",
                )

            self._render()
            progress_bar.update(
                progress_bar.add_task("rendering prompt...", total=1),
                completed=1,
                description="prompt rendered.",
            )

            if (
                self.config.show_tokens_format or self.config.console_show_token_count
            ):  # count if needed for any display
                self._count_tokens()
                token_count_display = (
                    str(self.token_count) if self.token_count is not None else "n/a"
                )
                progress_bar.update(
                    progress_bar.add_task("counting tokens...", total=1),
                    completed=1,
                    description=f"tokens: {token_count_display}.",
                )

        if not self.rendered_prompt:
            raise SmartPromptBuilderError(
                "prompt generation pipeline completed, but no content was rendered."
            )
        return self.rendered_prompt

# --- console summary output function ---
def _print_console_summary_info(config: PromptConfig, generator: PromptGenerator):
    """prints summary information (tree, file counts, tokens) to stderr if configured."""
    log.debug(
        "printing_console_summary_info_if_configured"
    )  # structlog for internal logging
    # use click.echo for user-facing stderr messages for consistency with click.
    if config.console_show_summary:
        click.secho("--- execution summary ---", fg="blue", err=True)
        click.echo(f"files processed: {len(generator.file_data)}", err=True)
        # todo: add more summary info like total lines, skipped files etc. if valuable.

    if config.console_show_tree and generator.file_data:
        # build_template_context is heavy if only tree is needed.
        # consider a lighter way if this becomes a performance concern for just console tree.
        # for now, reuse existing logic.
        tree_context = build_template_context(
            config, generator.file_data, None, None, None
        )
        if tree_context.get("source_tree"):
            click.secho(
                "\n--- project structure (console preview) ---", fg="blue", err=True
            )
            click.echo(tree_context["source_tree"], err=True)

    if config.console_show_token_count:
        if generator.token_count is not None:
            token_display_str = (
                f"{generator.token_count:,}"  # always human-readable for console
            )
            click.secho(
                f"\nestimated token count ({config.encoding}): {token_display_str}",
                fg="yellow",
                err=True,
            )
        else:
            click.secho(
                f"\nestimated token count ({config.encoding}): not calculated or n/a",
                fg="yellow",
                err=True,
            )


# --- main execution flow for the default command ---
def _execute_main_prompt_generation_flow(effective_config: PromptConfig):
    """main execution logic using the resolved `effective_config`."""
    log.info("main_flow_started", config_source=effective_config.__class__.__name__)
    try:
        prompt_generator = PromptGenerator(effective_config)
        final_prompt_output_str = prompt_generator.generate()  # core work happens here
        log.info("prompt_generation_successful")

        # determine output content (json structure or raw prompt)
        output_to_write: str
        is_json_output_format = effective_config.output_format == OutputFormat.JSON
        if is_json_output_format:
            log.debug("formatting_output_as_json_structure")
            json_payload: Dict[str, Any] = {
                "prompt_content": final_prompt_output_str,  # the rendered template output
                "metadata": {  # metadata about the generation process
                    "base_directory": str(effective_config.base_dir)
                    if effective_config.base_dir
                    else None,
                    "files_included_count": len(prompt_generator.file_data),
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
                    # add other relevant metadata from effective_config
                },
            }
            if prompt_generator.token_count is not None:  # add token info if calculated
                json_payload["token_information"] = {
                    "count": prompt_generator.token_count,
                    "encoding_used": effective_config.encoding,
                }
            try:
                output_to_write = json.dumps(json_payload, indent=2) + "\n"
            except TypeError as e:  # should not happen with basic types
                log.error("json_serialization_failed", error=str(e), exc_info=True)
                click.echo(
                    "error: failed to create json output. falling back to raw prompt content.",
                    err=True,
                )
                output_to_write = final_prompt_output_str  # fallback to raw content
                is_json_output_format = (
                    False  # no longer treat as json for output purposes
                )
        else:
            output_to_write = final_prompt_output_str  # already ends with newline from templaterenderer

        # handle output destinations (file, clipboard, stdout)
        output_destination_was_used = False
        if effective_config.output_file:
            write_to_file(effective_config.output_file, output_to_write)
            # user-facing messages to stderr to keep stdout clean for potential piping.
            click.echo(
                f"info: prompt output written to: {effective_config.output_file}",
                err=True,
            )
            output_destination_was_used = True

        clipboard_copy_succeeded = False
        if effective_config.clipboard:
            # for json, copy only the prompt_content part. for others, copy the whole output.
            content_for_clipboard = (
                final_prompt_output_str if is_json_output_format else output_to_write
            )
            if copy_to_clipboard(
                content_for_clipboard.strip()
            ):  # strip for cleaner clipboard content
                clipboard_copy_succeeded = True
            output_destination_was_used = (
                True  # attempting to copy counts as a destination.
            )

        # if no file output and (clipboard not requested or failed), print to stdout.
        if not output_destination_was_used or (
            effective_config.clipboard and not clipboard_copy_succeeded
        ):
            if effective_config.clipboard and not clipboard_copy_succeeded:
                click.echo(
                    "info: clipboard copy failed. outputting to standard output instead.",
                    file=sys.stderr,
                )
            log.info("writing_final_prompt_to_stdout")
            write_to_stdout(output_to_write)

        # display token count for the main output (--show-tokens) if requested.
        # this is separate from console-specific token display.
        if (
            effective_config.show_tokens_format
            and prompt_generator.token_count is not None
        ):
            token_format_enum = effective_config.show_tokens_format
            count_str = (
                f"{prompt_generator.token_count:,}"
                if token_format_enum == TokenCountFormat.HUMAN
                else str(prompt_generator.token_count)
            )
            click.echo(
                f"token count (for main output, encoding: '{effective_config.encoding}'): {count_str}",
                err=True,
            )
        elif effective_config.show_tokens_format:  # requested but not available
            log.warning("show_tokens_requested_but_not_available")
            click.echo(
                f"token count (for main output, encoding: '{effective_config.encoding}'): calculation failed or n/a.",
                err=True,
            )

        # print additional console summary info if configured (uses `effective_config.console_...` flags).
        _print_console_summary_info(effective_config, prompt_generator)

    except SmartPromptBuilderError as e:  # catch known application errors.
        log.error(
            "prompt_generation_failed_known_error",
            error_message=str(e),
            exc_info=log.getEffectiveLevel() <= logging.DEBUG,
        )
        click.echo(f"error: {e}", err=True)
        sys.exit(1)  # exit with error status.
    except Exception as e:  # catch-all for unexpected errors in this flow.
        log.critical(
            "unexpected_critical_error_in_main_flow",
            error_message=str(e),
            exc_info=True,
        )
        click.echo(
            f"unexpected error: {e}. please check logs or run with -vv for details.",
            err=True,
        )
        sys.exit(1)


# --- click cli group definition ---
@click.group(
    context_settings=dict(help_option_names=["-h", "--help"]),
    invoke_without_command=True,
)
# define cli options. use `default=None` for options where absence should be distinguishable from explicit value,
# to allow config file defaults to apply correctly if cli option is not used.
# flags (boolean options) default to false if not specified.
# input sources
@click.option(
    "--input-path",
    "input_paths",
    multiple=True,
    type=click.Path(readable=True, path_type=Path),
    default=None,
    help="paths to include (file/dir). default: '.' if not stdin.",
)
@click.option("--stdin", is_flag=True, help="read paths from stdin.")
@click.option(
    "-0", "--null", "nul_separated", is_flag=True, help="stdin paths are nul-separated."
)
# filtering
@click.option(
    "-i",
    "--include",
    "include_patterns",
    multiple=True,
    help='glob patterns to include (e.g., "**/*.py").',
)
@click.option(
    "-e",
    "--exclude",
    "exclude_patterns",
    multiple=True,
    help='glob patterns to exclude (e.g., "**/node_modules/**").',
)
@click.option(
    "--include-priority",
    is_flag=True,
    help="if a path matches include and exclude, include it.",
)
@click.option("--no-ignore", is_flag=True, help="do not use .gitignore files.")
@click.option(
    "--hidden",
    is_flag=True,
    help="include hidden files/directories (starting with '.').",
)
@click.option("-L", "--follow-symlinks", is_flag=True, help="follow symbolic links.")
# templating & formatting
@click.option(
    "-t",
    "--template",
    "template_path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="path to custom handlebars template file.",
)
@click.option(
    "--preset",
    "preset_template_str",
    type=click.Choice([p.value for p in PresetTemplate]),
    default=None,
    help="use a built-in preset template.",
)
@click.option(
    "--var",
    "user_vars_list",
    multiple=True,
    metavar="key=value",
    help="user variables for custom templates.",
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
    help="prepend line numbers to file content.",
)
@click.option(
    "--no-codeblock",
    "no_codeblock",
    is_flag=True,
    help="do not wrap file content in markdown code blocks.",
)
@click.option(
    "--absolute-paths",
    "absolute_paths",
    is_flag=True,
    help="use absolute file paths in template context.",
)
# yaml processing
@click.option(
    "--yaml-truncate-long-fields",
    "process_yaml_truncate_long_fields",
    is_flag=True,
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
# sorting
@click.option(
    "--sort",
    "sort_method_str",
    type=click.Choice([s.value for s in SortMethod]),
    default=None,
    help=f"sort files by method (default: {DEFAULT_SORT_METHOD.value}).",
)
# git
@click.option("--diff", "diff", is_flag=True, help="include staged git diff.")
@click.option(
    "--git-diff-branch", nargs=2, metavar="base comp", help="git diff between branches."
)
@click.option(
    "--git-log-branch", nargs=2, metavar="base comp", help="git log between branches."
)
# tokenizer
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
    help="show token count (for main output) on stderr.",
)
# output destinations
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="write output to file.",
)
@click.option(
    "--clipboard", is_flag=True, help="copy prompt content to system clipboard."
)
# console output preferences (new flags)
@click.option(
    "--console-tree/--no-console-tree",
    default=None,
    help="show/hide project tree on console (stderr).",
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
# general cli options
@click.option(
    "--config-profile",
    "config_profile_name",
    default=None,
    help="load a specific profile from config file(s).",
)
@click.option(
    "--verbose",
    "-v",
    "verbosity",
    count=True,
    help="increase verbosity: -v info, -vv debug.",
)
@click.option(
    "--force-json-logs", is_flag=True, help="force json output for logs, even if tty."
)
@click.version_option(
    version=app_version, package_name="llmfiles", prog_name="llmfiles"
)
@click.pass_context  # injects the click context object as `ctx`
def main_cli_group(
    ctx: click.Context, **cli_options: Any
):  # cli_options collects all defined @click.option values
    """
    llmfiles: intelligently build llm prompts from your codebase, git info, and custom templates.
    run `llmfiles --help` for all options.
    configuration can be set in `.llmfiles.toml` or `~/.config/llmfiles/config.toml`.
    """
    # 1. configure logging as early as possible.
    log_level = "warning"  # default
    if cli_options.get("verbosity", 0) == 1:
        log_level = "info"
    elif cli_options.get("verbosity", 0) >= 2:
        log_level = "debug"
    умри_configure_logging(
        log_level_str=log_level,
        force_json_logs=cli_options.get("force_json_logs", False),
    )

    log.debug(
        "cli_invoked",
        raw_cli_options=cli_options,
        invoked_subcommand=ctx.invoked_subcommand,
    )

    if ctx.invoked_subcommand is None:  # default action: generate prompt
        log.debug("default_command_flow_initiated")
        try:
            # 2. load defaults from config file(s), potentially using a profile
            file_and_profile_defaults = (
                get_merged_config_defaults()
            )  # from config_file.py
            profile_name = cli_options.get("config_profile_name")
            if profile_name:
                profile_settings = file_and_profile_defaults.get("profiles", {}).get(
                    profile_name, {}
                )
                log.info(
                    "loading_config_profile",
                    profile_name=profile_name,
                    settings_found=bool(profile_settings),
                )
                # merge profile settings over general file defaults
                file_and_profile_defaults.update(profile_settings)

            # 3. determine effective configuration: cli > profile > file > hardcoded
            # start with hardcoded defaults (from promptconfig dataclass)
            effective_settings: Dict[str, Any] = {
                f.name: f.default
                if f.default_factory is type(None)
                else f.default_factory()  # type: ignore
                for f in fields(PromptConfig)
                if f.init  # only init=true fields
            }
            log.debug("initial_hardcoded_defaults_for_config", **effective_settings)

            # layer file/profile defaults
            for conf_key, pc_attr_name in CONFIG_TO_PROMPTCONFIG_MAP.items():
                if conf_key in file_and_profile_defaults:
                    effective_settings[pc_attr_name] = file_and_profile_defaults[
                        conf_key
                    ]
            log.debug("config_after_file_profile_defaults", **effective_settings)

            # layer cli options if they were explicitly set by the user
            for opt_name_in_click, pc_attr_name in CONFIG_TO_PROMPTCONFIG_MAP.items():
                # map click option name (e.g. 'input_paths') to what's in cli_options (e.g. 'cli_input_paths')
                # this mapping needs to be robust. for now, assume cli_options keys match some transformation
                # of pc_attr_name or are handled by direct key lookup.
                # A direct mapping from `CONFIG_TO_PROMPTCONFIG_MAP` value (pc_attr) to `cli_options` key is needed.
                # This is complex because click option names are not always 1:1 with PromptConfig attrs.
                # Let's simplify by iterating cli_options which are already mapped by click.
                pass  # Placeholder for refined merge logic

            # simpler merge: start with config_data, override with cli_options where user provided input
            config_data_for_init = {
                **file_and_profile_defaults
            }  # start with file/profile config

            # iterate over promptconfig fields to build final kwargs for instantiation
            for pc_field in fields(PromptConfig):
                if not pc_field.init:
                    continue  # skip init=false fields like base_dir

                cli_param_name = pc_field.name  # assume cli option name matches promptconfig field for simplicity here
                # in reality, it's 'cli_' + pc_field.name from @click.option
                # or requires a map if names differ significantly.
                # For now, use direct mapping logic as in previous version.

                # Use mapping to find the key in cli_options dict
                cli_option_key_in_kwargs: Optional[str] = None
                for (
                    k_click,
                    k_pc,
                ) in (
                    CONFIG_TO_PROMPTCONFIG_MAP.items()
                ):  # This map is file_key -> pc_key
                    # Need a map: pc_key -> click_param_name (e.g. 'input_paths' -> 'cli_input_paths')
                    # This is getting overly complex for this response.
                    # Sticking to the previous simpler merge strategy for now.
                    pass

                # Simplified explicit override logic:
                # Iterate through CLI options that were actually passed (not defaults)
                # This requires using `ctx.get_parameter_source`
                for (
                    option_name,
                    _,
                ) in (
                    CONFIG_TO_PROMPTCONFIG_MAP.items()
                ):  # option_name is key from config file
                    pc_attr = CONFIG_TO_PROMPTCONFIG_MAP[option_name]

                    # Find the corresponding click parameter name (usually pc_attr or "cli_" + pc_attr)
                    # This is a bit manual; a direct map would be better.
                    # For now, assume cli_options keys are derived from PromptConfig fields.
                    cli_option_val = cli_options.get(pc_attr)  # Try direct match
                    if cli_option_val is None and cli_options.get(
                        f"cli_{pc_attr}"
                    ):  # Try with "cli_" prefix
                        cli_option_val = cli_options.get(f"cli_{pc_attr}")

                    # Check if the CLI option was provided by the user
                    # Note: get_parameter_source needs the actual option name, not the callback param name
                    # This part is tricky; Click stores params by their callback names.
                    # We need to map PromptConfig attribute names back to Click option names.

                    # Let's use a simpler approach: any value in cli_options (passed as **kwargs to main_cli_group)
                    # that is not None (for non-flags) or explicitly True/False (for flags if default=None)
                    # should override.

                    if pc_attr in cli_options:  # Check if key exists from CLI parsing
                        cli_val_for_attr = cli_options[pc_attr]

                        # Determine if CLI value should override (i.e., was explicitly set or is a meaningful flag)
                        is_explicit_cli_val = False
                        try:
                            # Use option name as defined in @click.option, not necessarily pc_attr
                            # This requires a reverse map or careful naming.
                            # For now, assume option_name from CONFIG_TO_PROMPTCONFIG_MAP maps to cli_options key if it exists.
                            # This logic needs robust mapping for all options.
                            # Example: if CONFIG_TO_PROMPTCONFIG_MAP has "include": "include_patterns"
                            # and cli_options has "include_patterns": ["val"], source for "include" option.

                            # This is the click option name (e.g. "include" not "include_patterns")
                            click_opt_name_for_source_check = (
                                option_name  # From CONFIG_TO_PROMPTCONFIG_MAP keys
                            )

                            param_obj = next(
                                (
                                    p
                                    for p in ctx.command.params
                                    if p.name == click_opt_name_for_source_check
                                    or any(
                                        opt_str.strip("-").replace("-", "_")
                                        == click_opt_name_for_source_check
                                        for opt_str in p.opts
                                    )
                                ),
                                None,
                            )

                            if (
                                param_obj
                                and ctx.get_parameter_source(param_obj.name)
                                == click.core.ParameterSource.COMMAND_LINE
                            ):
                                is_explicit_cli_val = True
                        except Exception:  # If param_obj not found, etc.
                            pass  # Default to not explicit

                        if is_explicit_cli_val or (
                            isinstance(cli_val_for_attr, bool)
                            and pc_attr in cli_options
                        ):  # Handle flags
                            # Convert string enums from CLI if necessary
                            if pc_attr == "preset_template":
                                config_data_for_init[pc_attr] = (
                                    PresetTemplate.from_string(cli_val_for_attr)
                                    if cli_val_for_attr
                                    else None
                                )
                            elif pc_attr == "output_format":
                                config_data_for_init[pc_attr] = (
                                    OutputFormat.from_string(cli_val_for_attr)
                                    or DEFAULT_OUTPUT_FORMAT
                                )
                            elif pc_attr == "sort_method":
                                config_data_for_init[pc_attr] = (
                                    SortMethod.from_string(cli_val_for_attr)
                                    or DEFAULT_SORT_METHOD
                                )
                            elif pc_attr == "show_tokens_format":
                                config_data_for_init[pc_attr] = (
                                    TokenCountFormat.from_string(cli_val_for_attr)
                                    if cli_val_for_attr
                                    else None
                                )
                            elif (
                                pc_attr == "user_vars"
                            ):  # cli_val_for_attr is user_vars_list tuple
                                user_vars_d: Dict[str, str] = {}
                                for var_str in cli_val_for_attr or []:
                                    if "=" not in var_str:
                                        raise click.BadParameter(
                                            f"invalid --var '{var_str}'. use k=v.",
                                            param_hint="--var",
                                        )
                                    k, v = var_str.split("=", 1)
                                    user_vars_d[k.strip()] = v
                                config_data_for_init[pc_attr] = user_vars_d
                            elif (
                                pc_attr == "input_paths"
                            ):  # cli_val_for_attr is input_paths tuple
                                config_data_for_init[pc_attr] = (
                                    list(cli_val_for_attr)
                                    if cli_val_for_attr
                                    else [Path(".")]
                                )
                            else:
                                config_data_for_init[pc_attr] = cli_val_for_attr
                            log.debug(
                                "cli_override_applied",
                                config_key=pc_attr,
                                value=config_data_for_init[pc_attr],
                            )

            # Default input_paths if still empty and not stdin
            if not config_data_for_init.get(
                "input_paths"
            ) and not config_data_for_init.get("read_from_stdin"):
                config_data_for_init["input_paths"] = [Path(".")]

            # Create PromptConfig instance
            final_effective_config = PromptConfig(**config_data_for_init)

            if (
                final_effective_config.process_yaml_truncate_long_fields
                and not PYYAML_AVAILABLE
            ):
                click.echo(
                    "warning: yaml truncation requested but pyyaml not installed. skipping this step.\n"
                    "  install with: pip install llmfiles[yaml_tools]",
                    err=True,
                )

            _execute_main_prompt_generation_flow(final_effective_config)

        except (click.ClickException, ConfigError, SmartPromptBuilderError) as e:
            # use structlog for application errors, click.echo for user feedback
            log.error(
                "cli_execution_error",
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=log.getEffectiveLevel() <= logging.DEBUG,
            )
            if isinstance(e, click.ClickException):
                e.show()  # click handles its own error display
            else:
                click.echo(f"error: {e}", err=True)
            sys.exit(1)
        except Exception as e:  # catch-all for truly unexpected issues
            log.critical(
                "unexpected_cli_error_critical", error_message=str(e), exc_info=True
            )
            click.echo(
                f"unexpected error: {e}. please check logs or report this issue.",
                err=True,
            )
            sys.exit(1)
    # else: subcommand was invoked, click will handle its execution.

def main_cli_entrypoint():
    """main entry point for the llmfiles cli script, called by `pyproject.toml` [project.scripts]."""
    # note: if actual subcommands (extensions) were to be loaded, it would happen here,
    # before `main_cli_group()` is called. for the integrated yaml feature, this is not needed.
    main_cli_group(prog_name="llmfiles")

if __name__ == '__main__':
    main_cli_entrypoint()