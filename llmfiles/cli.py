# llmfiles/cli.py
"""Command Line Interface for llmfiles, using Click."""

import click
import sys
import logging
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

import tiktoken  # type: ignore
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from .config import (
    PromptConfig,
    SortMethod,
    OutputFormat,
    TokenCountFormat,
    PresetTemplate,
    DEFAULT_YAML_TRUNCATION_PLACEHOLDER,
    DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN,
)
from .discovery import discover_paths
from .processing import (
    process_file_content,
    PYYAML_AVAILABLE,
)  # Import PYYAML_AVAILABLE
from .git_utils import get_diff, get_diff_branches, get_log_branches, check_is_git_repo
from .templating import TemplateRenderer, build_template_context
from .output import write_to_stdout, write_to_file, copy_to_clipboard
from .exceptions import SmartPromptBuilderError, TokenizerError, ConfigError
from . import __version__ as app_version  # Import version from __init__.py

logger = logging.getLogger(__name__)  # Logger for this module: llmfiles.cli


class PromptGenerator:
    """Orchestrates prompt generation steps."""
    def __init__(self, config: PromptConfig):
        self.config, self.file_data = config, []
        self.git_diff_data, self.git_diff_branches_data, self.git_log_branches_data = (
            None,
            None,
            None,
        )
        self.rendered_prompt, self.token_count = None, None

    def _discover_paths(self) -> List[Path]:
        logger.info("Step 1: Discovering paths...")
        paths = list(discover_paths(self.config))
        logger.info(f"Discovery found {len(paths)} potential paths.")
        return paths

    def _process_contents(self, paths: List[Path], progress: Progress) -> None:
        logger.info(f"Step 2: Processing content for {len(paths)} paths...")
        task = progress.add_task("Processing files...", total=len(paths))
        processed, skipped = 0, 0
        for p_obj in paths:
            res = process_file_content(p_obj, self.config)
            if res:
                content, raw_content, mod_time = res
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
                    entry["mod_time"] = mod_time
                self.file_data.append(entry)
                processed += 1
            else:
                skipped += 1
            progress.update(task, advance=1)
        progress.update(
            task, description=f"Processed {processed} files (skipped {skipped})."
        )

    def _sort_data(self) -> None:
        logger.info(
            f"Step 3: Sorting {len(self.file_data)} entries by '{self.config.sort_method.value}'..."
        )
        key_fn: Optional[Any] = None
        rev = False
        sm = self.config.sort_method
        if sm == SortMethod.NAME_ASC:
            key_fn = lambda x: x["relative_path"]
        elif sm == SortMethod.NAME_DESC:
            key_fn, rev = lambda x: x["relative_path"], True
        elif sm == SortMethod.DATE_ASC:
            key_fn = lambda x: x.get("mod_time", float("inf"))
        elif sm == SortMethod.DATE_DESC:
            key_fn, rev = lambda x: x.get("mod_time", float("-inf")), True
        if key_fn:
            try:
                self.file_data.sort(key=key_fn, reverse=rev)
            except Exception as e:
                logger.warning(f"Sort failed: {e}. Proceeding unsorted.")

    def _fetch_git(self) -> None:
        logger.info("Step 4: Fetching Git info (if configured)...")
        if not self.config.base_dir or not check_is_git_repo(self.config.base_dir):
            if any(
                [
                    self.config.diff,
                    self.config.git_diff_branch,
                    self.config.git_log_branch,
                ]
            ):
                logger.warning(
                    f"Not a Git repo or Git unavailable at {self.config.base_dir}. Skipping Git ops."
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
            logger.error(f"Git op failed: {e}. Info might be missing.")

    def _render(self) -> None:
        logger.info("Step 5: Rendering prompt...")
        ctx = build_template_context(
            self.config,
            self.file_data,
            self.git_diff_data,
            self.git_diff_branches_data,
            self.git_log_branches_data,
        )
        self.rendered_prompt = TemplateRenderer(self.config).render(ctx)

    def _count_tokens(self) -> None:
        if self.config.show_tokens_format and self.rendered_prompt:
            logger.info(f"Step 6: Counting tokens (enc: '{self.config.encoding}')...")
            try:
                enc = tiktoken.get_encoding(
                    self.config.encoding
                )  # Validated by encoding_for_model if desired
                self.token_count = len(
                    enc.encode(self.rendered_prompt, disallowed_special=())
                )
                logger.info(f"Token count: {self.token_count}")
            except Exception as e:
                raise TokenizerError(
                    f"Token calculation failed for '{self.config.encoding}': {e}"
                )

    def generate(self) -> str:
        """Main pipeline to generate the prompt."""
        use_progress = logger.getEffectiveLevel() <= logging.INFO
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            transient=False,
            disable=not use_progress,
        ) as prog:
            paths = self._discover_paths()
            prog.update(
                prog.add_task("Discovering paths...", total=1),
                completed=1,
                description=f"Found {len(paths)} potential paths.",
            )
            if paths:
                self._process_contents(paths, prog)
            else:
                prog.update(
                    prog.add_task("Processing files...", total=1),
                    completed=1,
                    description="No files to process.",
                )
            self._sort_data()
            prog.update(
                prog.add_task("Sorting files...", total=1),
                completed=1,
                description=f"Sorted {len(self.file_data)} files.",
            )
            if any(
                [
                    self.config.diff,
                    self.config.git_diff_branch,
                    self.config.git_log_branch,
                ]
            ):
                self._fetch_git()
                prog.update(
                    prog.add_task("Fetching Git...", total=1),
                    completed=1,
                    description="Git info fetched.",
                )
            self._render()
            prog.update(
                prog.add_task("Rendering...", total=1),
                completed=1,
                description="Prompt rendered.",
            )
            if self.config.show_tokens_format:
                self._count_tokens()
                tc_str = str(self.token_count) if self.token_count else "N/A"
                prog.update(
                    prog.add_task("Counting tokens...", total=1),
                    completed=1,
                    description=f"Tokens: {tc_str}.",
                )
        if not self.rendered_prompt:
            raise SmartPromptBuilderError("Prompt generation resulted in no content.")
        return self.rendered_prompt

def _run_main_flow(config: PromptConfig):
    """Executes the default prompt generation and output logic."""
    try:
        generator = PromptGenerator(config)
        final_prompt = generator.generate()

        output_content: str
        is_json = config.output_format == OutputFormat.JSON
        if is_json:
            payload: Dict[str, Any] = {
                "prompt_content": final_prompt,
                "metadata": {
                    "base_dir": str(config.base_dir),
                    "files_count": len(generator.file_data),
                },
            }
            if generator.token_count is not None:
                payload["token_info"] = {
                    "count": generator.token_count,
                    "encoding": config.encoding,
                }
            try:
                output_content = json.dumps(payload, indent=2) + "\n"
            except TypeError as e:
                logger.error(f"JSON serialization failed: {e}", exc_info=True)
                output_content, is_json = final_prompt, False  # Fallback
        else:
            output_content = final_prompt

        # Output handling
        out_done = False
        if config.output_file:
            write_to_file(config.output_file, output_content)
            click.echo(f"Output to: {config.output_file}", err=True)
            out_done = True
        cb_ok = False
        if config.clipboard:
            cb_content = final_prompt if is_json else output_content
            if copy_to_clipboard(cb_content.strip()):
                cb_ok = True
            out_done = True
        if not out_done or (config.clipboard and not cb_ok):
            if config.clipboard and not cb_ok:
                click.echo(
                    "Clipboard copy failed. Outputting to stdout.", file=sys.stderr
                )
            write_to_stdout(output_content)

        if config.show_tokens_format and generator.token_count is not None:
            fmt = (
                TokenCountFormat.HUMAN
                if config.show_tokens_format == TokenCountFormat.HUMAN
                else TokenCountFormat.RAW
            )
            tc_disp = (
                f"{generator.token_count:,}"
                if fmt == TokenCountFormat.HUMAN
                else str(generator.token_count)
            )
            click.echo(f"Token count ({config.encoding}): {tc_disp}", err=True)
        elif config.show_tokens_format:
            click.echo(f"Token count ({config.encoding}): N/A", err=True)

    except SmartPromptBuilderError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.critical("Unexpected error in main flow.", exc_info=True)
        click.echo(f"UNEXPECTED ERROR: {e}", err=True)
        sys.exit(1)

@click.group(
    context_settings=dict(help_option_names=["-h", "--help"]),
    invoke_without_command=True,
)
@click.option(
    "--input-path",
    "cli_input_paths",
    multiple=True,
    type=click.Path(readable=True, path_type=Path),
    help="Paths to include (file/dir). Default: '.' if not stdin.",
)
@click.option(
    "--stdin", "cli_read_from_stdin", is_flag=True, help="Read paths from stdin."
)
@click.option(
    "-0",
    "--null",
    "cli_nul_separated_stdin",
    is_flag=True,
    help="Stdin paths are NUL-separated.",
)
@click.option(
    "-i",
    "--include",
    "cli_include_patterns",
    multiple=True,
    help="Glob patterns to include.",
)
@click.option(
    "-e",
    "--exclude",
    "cli_exclude_patterns",
    multiple=True,
    help="Glob patterns to exclude.",
)
@click.option(
    "--include-priority",
    "cli_force_include_over_exclude",
    is_flag=True,
    help="--include overrides --exclude.",
)
@click.option(
    "--no-ignore",
    "cli_disable_gitignore",
    is_flag=True,
    help="Ignore .gitignore files.",
)
@click.option(
    "--hidden",
    "cli_include_hidden_files",
    is_flag=True,
    help="Include hidden files/dirs.",
)
@click.option(
    "-L",
    "--follow-symlinks",
    "cli_traverse_symlinks",
    is_flag=True,
    help="Follow symlinks.",
)
@click.option(
    "-t",
    "--template",
    "cli_custom_template_path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="Path to custom Handlebars template.",
)
@click.option(
    "--preset",
    "cli_selected_preset_template_str",
    type=click.Choice([p.value for p in PresetTemplate]),
    help="Use a built-in preset template.",
)
@click.option(
    "--var",
    "cli_user_template_variables_list",
    multiple=True,
    metavar="K=V",
    help="User variables for templates.",
)
@click.option(
    "-F",
    "--output-format",
    "cli_fallback_output_format_str",
    type=click.Choice([f.value for f in OutputFormat]),
    default=OutputFormat.MARKDOWN.value,
    show_default=True,
    help="Fallback output format.",
)
@click.option(
    "-n",
    "--line-numbers",
    "cli_show_line_numbers",
    is_flag=True,
    help="Prepend line numbers.",
)
@click.option(
    "--no-codeblock",
    "cli_disable_markdown_codeblocks",
    is_flag=True,
    help="No Markdown code blocks.",
)
@click.option(
    "--absolute-paths",
    "cli_use_absolute_paths_in_output",
    is_flag=True,
    help="Use absolute file paths in context.",
)
@click.option(
    "--yaml-truncate-long-fields",
    "cli_process_yaml_truncate_long_fields",
    is_flag=True,
    help="Truncate long fields in YAML (needs PyYAML).",
)
@click.option(
    "--yaml-placeholder",
    "cli_yaml_truncate_placeholder",
    default=DEFAULT_YAML_TRUNCATION_PLACEHOLDER,
    show_default=True,
    help="Placeholder for truncated YAML content.",
)
@click.option(
    "--yaml-max-len",
    "cli_yaml_truncate_content_max_len",
    type=int,
    default=DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN,
    show_default=True,
    help="Max length for YAML field truncation.",
)
@click.option(
    "--sort",
    "cli_file_sort_method_str",
    type=click.Choice([s.value for s in SortMethod]),
    default=SortMethod.NAME_ASC.value,
    show_default=True,
    help="Sort files by method.",
)
@click.option(
    "--diff",
    "cli_include_staged_git_diff",
    is_flag=True,
    help="Include staged Git diff.",
)
@click.option(
    "--git-diff-branch",
    "cli_diff_between_git_branches",
    nargs=2,
    metavar="BASE COMP",
    help="Git diff between branches.",
)
@click.option(
    "--git-log-branch",
    "cli_log_between_git_branches",
    nargs=2,
    metavar="BASE COMP",
    help="Git log between branches.",
)
@click.option(
    "-c",
    "--encoding",
    "cli_token_counter_encoding",
    default="cl100k",
    show_default=True,
    help="Tiktoken encoding for token count.",
)
@click.option(
    "--show-tokens",
    "cli_display_token_count_format_str",
    type=click.Choice([f.value for f in TokenCountFormat]),
    help="Show token count on stderr.",
)
@click.option(
    "-o",
    "--output",
    "cli_output_to_file_path_str",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Write output to file.",
)
@click.option(
    "--clipboard",
    "cli_copy_to_system_clipboard",
    is_flag=True,
    help="Copy prompt to clipboard.",
)
@click.option(
    "--verbose",
    "-v",
    "cli_verbosity_level",
    count=True,
    help="Verbosity: -v INFO, -vv DEBUG.",
)
@click.version_option(
    version=app_version, package_name="llmfiles", prog_name="llmfiles"
)
@click.pass_context
def main_cli_group(ctx: click.Context, cli_verbosity_level: int, **kwargs: Any):
    """llmfiles: Build LLM prompts from codebases, git info, and templates."""
    log_level = logging.WARNING
    if cli_verbosity_level == 1:
        log_level = logging.INFO
    elif cli_verbosity_level >= 2:
        log_level = logging.DEBUG

    app_logger = logging.getLogger("llmfiles")
    if (
        not app_logger.handlers
    ):  # Setup logger only if not already configured (e.g. by tests)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(levelname)-8s [%(name)s] %(message)s")
        )
        app_logger.addHandler(handler)
    app_logger.setLevel(log_level)
    if cli_verbosity_level > 0:
        logger.info(f"Log level set to: {logging.getLevelName(log_level)}")

    if ctx.invoked_subcommand is None:  # Default action: generate prompt
        logger.debug("No subcommand; running default prompt generation.")
        try:
            paths = list(kwargs.get("cli_input_paths") or [])
            if not paths and not kwargs.get("cli_read_from_stdin"):
                paths = [Path(".")]  # Default to current dir

            user_vars: Dict[str, str] = {}
            for var_str in kwargs.get("cli_user_template_variables_list", []):
                if "=" not in var_str:
                    raise click.BadParameter(
                        f"Invalid --var '{var_str}'. Use KEY=VALUE.", param_hint="--var"
                    )
                k, v = var_str.split("=", 1)
                user_vars[k.strip()] = v

            config = PromptConfig(
                input_paths=paths,
                read_from_stdin=kwargs.get("cli_read_from_stdin", False),
                nul_separated=kwargs.get("cli_nul_separated_stdin", False),
                include_patterns=list(kwargs.get("cli_include_patterns", [])),
                exclude_patterns=list(kwargs.get("cli_exclude_patterns", [])),
                include_priority=kwargs.get("cli_force_include_over_exclude", False),
                no_ignore=kwargs.get("cli_disable_gitignore", False),
                hidden=kwargs.get("cli_include_hidden_files", False),
                follow_symlinks=kwargs.get("cli_traverse_symlinks", False),
                template_path=kwargs.get("cli_custom_template_path"),
                preset_template=PresetTemplate.from_string(
                    kwargs["cli_selected_preset_template_str"]
                )
                if kwargs.get("cli_selected_preset_template_str")
                else None,
                user_vars=user_vars,
                output_format=OutputFormat.from_string(
                    kwargs["cli_fallback_output_format_str"]
                )
                or OutputFormat.MARKDOWN,
                line_numbers=kwargs.get("cli_show_line_numbers", False),
                no_codeblock=kwargs.get("cli_disable_markdown_codeblocks", False),
                absolute_paths=kwargs.get("cli_use_absolute_paths_in_output", False),
                process_yaml_truncate_long_fields=kwargs.get(
                    "cli_process_yaml_truncate_long_fields", False
                ),
                yaml_truncate_placeholder=kwargs.get(
                    "cli_yaml_truncate_placeholder", DEFAULT_YAML_TRUNCATION_PLACEHOLDER
                ),
                yaml_truncate_content_max_len=kwargs.get(
                    "cli_yaml_truncate_content_max_len",
                    DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN,
                ),
                sort_method=SortMethod.from_string(kwargs["cli_file_sort_method_str"])
                or SortMethod.NAME_ASC,
                diff=kwargs.get("cli_include_staged_git_diff", False),
                git_diff_branch=kwargs.get("cli_diff_between_git_branches") or None,
                git_log_branch=kwargs.get("cli_log_between_git_branches") or None,
                encoding=kwargs.get("cli_token_counter_encoding", "cl100k"),
                show_tokens_format=TokenCountFormat.from_string(
                    kwargs["cli_display_token_count_format_str"]
                )
                if kwargs.get("cli_display_token_count_format_str")
                else None,
                output_file=kwargs.get("cli_output_to_file_path_str"),
                clipboard=kwargs.get("cli_copy_to_system_clipboard", False),
            )
            if config.process_yaml_truncate_long_fields and not PYYAML_AVAILABLE:
                click.echo(
                    "WARNING: YAML truncation requested (--yaml-truncate-long-fields) but PyYAML not installed. Skipping. "
                    "Install with: pip install llmfiles[yaml_tools]",
                    err=True,
                )
            _run_main_flow(config)
        except (click.ClickException, ConfigError, SmartPromptBuilderError) as e:
            logger.debug(
                "CLI Error details:",
                exc_info=True if log_level <= logging.DEBUG else False,
            )
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        except Exception as e:
            logger.critical("Unexpected CLI error.", exc_info=True)
            click.echo(f"UNEXPECTED ERROR: {e}. Report this.", err=True)
            sys.exit(1)
    else:
        logger.debug(f"Subcommand '{ctx.invoked_subcommand}' invoked.")

def main_cli_entrypoint():
    """Main entry point for the CLI script."""
    # If an extension system for *other* subcommands existed, it would be loaded here.
    # For the integrated YAML feature, no separate extension loading is needed.
    main_cli_group(prog_name="llmfiles")

if __name__ == '__main__':
    main_cli_entrypoint()