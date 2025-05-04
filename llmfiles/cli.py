# llmfiles/cli.py
"""Command Line Interface using Click."""
import click
import sys
import logging
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

import tiktoken
from rich.progress import Progress, SpinnerColumn, TextColumn
# Assuming logger is configured at the root or here
logging.basicConfig(
    level=logging.WARNING, format="%(levelname)-8s [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Relative imports for sibling modules
from .config import (
    PromptConfig,
    SortMethod,
    OutputFormat,
    TokenCountFormat,
    PresetTemplate,
)
from .discovery import discover_paths
from .processing import process_file_content
from .git_utils import get_diff, get_diff_branches, get_log_branches, check_is_git_repo
from .templating import TemplateRenderer, build_template_context
from .output import write_to_stdout, write_to_file, copy_to_clipboard
from .exceptions import SmartPromptBuilderError, TokenizerError, ConfigError

# --- Generator Class ---
class PromptGenerator:
    def __init__(self, config: PromptConfig):
        self.config = config
        self.file_data: List[Dict[str, Any]] = []
        self.git_diff_data: Optional[str] = None
        self.git_diff_branches_data: Optional[str] = None
        self.git_log_branches_data: Optional[str] = None
        self.rendered_prompt: Optional[str] = None
        self.token_count: Optional[int] = None

    def _discover_and_filter_paths(self):
        """Generates filtered paths based on config."""
        logger.info("Discovering and filtering paths...")
        yield from discover_paths(self.config)

    def _process_file_contents(self):
        """Reads and processes content for filtered files."""
        logger.info("Processing file contents...")
        temp_file_data = []
        processed_count = 0
        skipped_count = 0
        for path in self._discover_and_filter_paths():
            processed_data = process_file_content(path, self.config)
            if processed_data:
                content, raw_content, mod_time = processed_data
                if self.config.base_dir is None:
                    # This should ideally be caught during config validation
                    raise ConfigError("Base directory not set during file processing.")
                relative_path = path.relative_to(self.config.base_dir)
                abs_path_str = str(path)
                rel_path_str = str(relative_path)
                file_entry = {
                    "path": abs_path_str
                    if self.config.absolute_paths
                    else rel_path_str,
                    "relative_path": rel_path_str,
                    "content": content,
                    "raw_content": raw_content,
                    "extension": path.suffix[1:].lower() if path.suffix else "",
                    "mod_time": mod_time,
                }
                temp_file_data.append(file_entry)
                processed_count += 1
            else:
                skipped_count += 1
        self.file_data = temp_file_data
        logger.info(
            f"Finished processing files. Included: {processed_count}, Skipped: {skipped_count}"
        )

    def _sort_file_data(self):
        """Sorts the collected file data based on config."""
        logger.info(f"Sorting file data by {self.config.sort_method.value}...")
        sort_key = None
        reverse = False

        if self.config.sort_method == SortMethod.NAME_ASC:
            sort_key = lambda x: x["relative_path"]
        elif self.config.sort_method == SortMethod.NAME_DESC:
            sort_key = lambda x: x["relative_path"]
            reverse = True
        elif self.config.sort_method == SortMethod.DATE_ASC:
            sort_key = (
                lambda x: x.get("mod_time")
                if x.get("mod_time") is not None
                else float("inf")
            )
        elif self.config.sort_method == SortMethod.DATE_DESC:
            sort_key = (
                lambda x: x.get("mod_time")
                if x.get("mod_time") is not None
                else float("-inf")
            )
            reverse = True

        if sort_key:
            try:
                self.file_data.sort(key=sort_key, reverse=reverse)
                logger.debug("File data sorted.")
            except Exception as e:
                logger.warning(
                    f"Could not sort file data: {e}. Proceeding without sorting."
                )
        else:
            logger.debug("No sorting key defined, skipping sort.")


    def _fetch_git_info(self):
        """Fetches Git information if configured."""
        if self.config.base_dir is None:
            logger.warning("Base directory not set, cannot perform Git operations.")
            return

        repo_path = self.config.base_dir
        is_repo = check_is_git_repo(repo_path)
        if not is_repo:
            if (
                self.config.diff
                or self.config.git_diff_branch
                or self.config.git_log_branch
            ):
                logger.warning(
                    f"Path {repo_path} is not a git repository. Skipping git operations."
                )
            return

        try:
            if self.config.diff:
                logger.info("Fetching staged Git diff...")
                self.git_diff_data = get_diff(repo_path)
            if self.config.git_diff_branch:
                b1, b2 = self.config.git_diff_branch
                logger.info(f"Fetching Git diff between {b1} and {b2}...")
                self.git_diff_branches_data = get_diff_branches(repo_path, b1, b2)
            if self.config.git_log_branch:
                b1, b2 = self.config.git_log_branch
                logger.info(f"Fetching Git log between {b1} and {b2}...")
                self.git_log_branches_data = get_log_branches(repo_path, b1, b2)
        except SmartPromptBuilderError as e:
            logger.error(f"Git operation failed: {e}")
            # Decide whether to halt or continue without git info
            # For now, just log the error and continue

    def _render(self):
        """Builds context and renders the template."""
        logger.info("Rendering the final prompt...")
        context = build_template_context(
            self.config,
            self.file_data,
            self.git_diff_data,
            self.git_diff_branches_data,
            self.git_log_branches_data
        )
        renderer = TemplateRenderer(self.config)
        self.rendered_prompt = renderer.render(context)

    def _calculate_tokens(self):
        """Calculates token count if an encoding is specified."""
        if self.config.show_tokens_format and self.rendered_prompt:
            logger.info(f"Calculating token count using encoding: {self.config.encoding}")
            try:
                # Ensure encoding is valid before getting it
                tiktoken.encoding_for_model(
                    self.config.encoding
                )  # Raises ValueError if invalid
                enc = tiktoken.get_encoding(self.config.encoding)
                tokens = enc.encode(
                    self.rendered_prompt, disallowed_special=()
                )  # Allow special tokens during count
                self.token_count = len(tokens)
                logger.info(f"Token count: {self.token_count}")
            except ValueError:
                # This error comes from encoding_for_model if name is unknown
                raise TokenizerError(f"Invalid or unsupported encoding: {self.config.encoding}")
            except Exception as e:
                 raise TokenizerError(f"Failed to calculate tokens: {e}")


    def generate(self) -> str:
        """Orchestrates the prompt generation process."""
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task_discover = progress.add_task(
                "Discovering & Processing Files...", total=None
            )
            self._process_file_contents()  # Includes discovery yields and processing
            progress.update(
                task_discover,
                completed=1,
                description=f"Processed {len(self.file_data)} files.",
            )

            task_sort = progress.add_task("Sorting file data...", total=None)
            self._sort_file_data()
            progress.update(task_sort, completed=1, description="File data sorted.")

            if (
                self.config.diff
                or self.config.git_diff_branch
                or self.config.git_log_branch
            ):
                task_git = progress.add_task("Fetching Git info...", total=None)
                self._fetch_git_info()
                progress.update(task_git, completed=1, description="Git info fetched.")

            task_render = progress.add_task("Rendering prompt...", total=None)
            self._render()
            progress.update(task_render, completed=1, description="Prompt rendered.")

            if self.config.show_tokens_format:
                task_tokens = progress.add_task("Calculating tokens...", total=None)
                self._calculate_tokens()
                progress.update(
                    task_tokens,
                    completed=1,
                    description=f"Tokens calculated ({self.token_count}).",
                )

        if self.rendered_prompt is None:
            # This case should ideally be prevented by raising errors earlier
            raise SmartPromptBuilderError("Prompt generation failed to produce output.")
        return self.rendered_prompt


# --- Click CLI Definition ---
@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.argument(
    "input_paths_arg",
    nargs=-1,
    type=click.Path(exists=True, readable=True, path_type=Path),
)
@click.option(
    "--stdin",
    "read_from_stdin",
    is_flag=True,
    help="Read list of NUL or newline separated paths from stdin.",
)
@click.option(
    "-0",
    "--null",
    "nul_separated",
    is_flag=True,
    help="Input paths from stdin are NUL-separated (recommended for use with find ... -print0).",
)
@click.option(
    "-i",
    "--include",
    "include_patterns",
    multiple=True,
    help='Glob pattern(s) to include files/dirs (e.g., "*.py", "src/**"). Applied relative to input paths.',
)
@click.option(
    "-e",
    "--exclude",
    "exclude_patterns",
    multiple=True,
    help='Glob pattern(s) to exclude files/dirs (e.g., "**/__pycache__", "*.tmp"). Applied relative to input paths.',
)
@click.option(
    "--include-priority",
    is_flag=True,
    default=False,
    help="Include files matching both --include and --exclude.",
)
@click.option(
    "--no-ignore",
    is_flag=True,
    default=False,
    help="Ignore rules from .gitignore files.",
)
@click.option(
    "--hidden",
    is_flag=True,
    default=False,
    help='Include hidden files/directories (those starting with ".").',
)
@click.option(
    "-L",
    "--follow-symlinks",
    is_flag=True,
    default=False,
    help="Follow symbolic links during directory traversal.",
)
@click.option(
    "-t",
    "--template",
    "template_path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="Path to a custom Handlebars template file (overrides --preset and --output-format).",
)
@click.option(
    "--preset",
    "preset_template_str",
    type=click.Choice([p.value for p in PresetTemplate], case_sensitive=False),
    help="Use a built-in preset template (e.g., claude-optimal, generic-xml, default). Overrides --output-format.",
)
@click.option(
    "--var",
    "user_vars_list",
    multiple=True,
    metavar="KEY=VALUE",
    help="User variables for custom templates (e.g., --var project_name=MyLib).",
)
@click.option(
    "-F",
    "--output-format",
    type=click.Choice([f.value for f in OutputFormat], case_sensitive=False),
    default=OutputFormat.MARKDOWN.value,
    help="Fallback output format if no template/preset is specified (markdown, xml, json).",
)
@click.option('-n', '--line-numbers', is_flag=True, default=False, help='Prepend line numbers to code content.')
@click.option('--no-codeblock', is_flag=True, default=False, help='Do not wrap code content in Markdown code blocks.')
@click.option(
    "--absolute-paths",
    is_flag=True,
    default=False,
    help="Use absolute paths in output context instead of relative.",
)
@click.option(
    "--sort",
    "sort_method_str",
    type=click.Choice([s.value for s in SortMethod], case_sensitive=False),
    default=SortMethod.NAME_ASC.value,
    help="Sort included files by method (name_asc, name_desc, date_asc, date_desc).",
)
@click.option(
    "--diff",
    is_flag=True,
    default=False,
    help="Include staged Git diff (HEAD vs Index) if in a git repo.",
)
@click.option(
    "--git-diff-branch",
    nargs=2,
    metavar="BASE_BRANCH COMPARE_BRANCH",
    help="Include Git diff between two branches.",
)
@click.option(
    "--git-log-branch",
    nargs=2,
    metavar="BASE_BRANCH COMPARE_BRANCH",
    help="Include Git log between two branches.",
)
@click.option(
    "-c",
    "--encoding",
    default="cl100k",
    show_default=True,
    help="Tokenizer encoding for token count (e.g., cl100k, o200k, gpt2). See tiktoken docs.",
)
@click.option(
    "--show-tokens",
    "show_tokens_format_str",
    type=click.Choice([f.value for f in TokenCountFormat], case_sensitive=False),
    help="Show calculated token count on stderr (human or raw format). Requires a valid --encoding.",
)
@click.option('-o', '--output', 'output_file_str', type=click.Path(dir_okay=False, writable=True, path_type=Path), help='Write output to file instead of stdout.')
@click.option(
    "--clipboard",
    is_flag=True,
    default=False,
    help="Copy output to system clipboard (prompt content only, not JSON structure).",
)
@click.option('--verbose', '-v', count=True, help='Increase verbosity (-v for INFO, -vv for DEBUG).')
@click.version_option(
    package_name="llmfiles"
)  # Ensure package name matches pyproject.toml
def main_cli(
    input_paths_arg: Tuple[Path, ...],
    read_from_stdin: bool,
    nul_separated: bool,
    include_patterns: Tuple[str, ...],
    exclude_patterns: Tuple[str, ...],
    include_priority: bool,
    no_ignore: bool,
    hidden: bool,
    follow_symlinks: bool,
    template_path: Optional[Path],
    preset_template_str: Optional[str],
    user_vars_list: Tuple[str, ...],
    output_format: str,
    line_numbers: bool,
    no_codeblock: bool,
    absolute_paths: bool,
    sort_method_str: str,
    diff: bool,
    git_diff_branch: Optional[Tuple[str, str]],
    git_log_branch: Optional[Tuple[str, str]],
    encoding: str,
    show_tokens_format_str: Optional[str],
    output_file_str: Optional[Path],
    clipboard: bool,
    verbose: int,
):
    """
    llmfiles: Generate LLM prompts from codebases, git info, and templates.

    Provide file/directory paths as arguments or use --stdin to pipe them.
    Example: find . -name '*.py' -print0 | llmfiles --stdin -0 -o prompt.txt
    """
    # --- Logging Setup ---
    log_level = logging.WARNING
    if verbose == 1:
        log_level = logging.INFO
    elif verbose >= 2:
        log_level = logging.DEBUG
    logging.getLogger("llmfiles").setLevel(
        log_level
    )  # Target specific logger if modules use getLogger(__name__)
    # Set root logger level as well if needed, or configure handlers
    logging.getLogger().setLevel(log_level)
    logger.info(f"Log level set to {logging.getLevelName(log_level)}")

    # --- Input Validation and Config Building ---
    try:
        if not input_paths_arg and not read_from_stdin:
            raise click.UsageError(
                "Error: Must provide input paths as arguments or use --stdin."
            )
        if nul_separated and not read_from_stdin:
            raise click.UsageError("Error: -0/--null requires --stdin.")
        if template_path and preset_template_str:
            logger.warning(
                "Both --template and --preset provided. Custom --template will be used."
            )
        if output_file_str and output_file_str.is_dir():
            raise click.BadParameter(
                f"Output path '{output_file_str}' is a directory.",
                param_hint="--output",
            )

        user_vars: Dict[str, str] = {}
        for var_item in user_vars_list:
            if '=' not in var_item:
                 raise click.BadParameter(f"Invalid format for --var '{var_item}'. Use key=value.", param_hint='--var')
            key, value = var_item.split('=', 1)
            user_vars[key.strip()] = value

        output_format_enum = OutputFormat.from_string(output_format)
        sort_method_enum = SortMethod.from_string(sort_method_str)
        show_tokens_format_enum = (
            TokenCountFormat.from_string(show_tokens_format_str)
            if show_tokens_format_str
            else None
        )
        preset_template_enum = (
            PresetTemplate.from_string(preset_template_str)
            if preset_template_str
            else None
        )

        if not output_format_enum:
            raise click.BadParameter(
                f"Invalid output format: {output_format}", param_hint="--output-format"
            )
        if not sort_method_enum:
            raise click.BadParameter(
                f"Invalid sort method: {sort_method_str}", param_hint="--sort"
            )
        if show_tokens_format_str and not show_tokens_format_enum:
            raise click.BadParameter(
                f"Invalid token format: {show_tokens_format_str}",
                param_hint="--show-tokens",
            )
        if preset_template_str and not preset_template_enum:
            raise click.BadParameter(
                f"Invalid preset template: {preset_template_str}", param_hint="--preset"
            )

        config = PromptConfig(
            input_paths=list(input_paths_arg),
            read_from_stdin=read_from_stdin,
            nul_separated=nul_separated,
            include_patterns=list(include_patterns),
            exclude_patterns=list(exclude_patterns),
            include_priority=include_priority,
            no_ignore=no_ignore,
            hidden=hidden,
            follow_symlinks=follow_symlinks,
            template_path=template_path,
            preset_template=preset_template_enum,
            user_vars=user_vars,
            output_format=output_format_enum,
            line_numbers=line_numbers,
            no_codeblock=no_codeblock,
            absolute_paths=absolute_paths,
            sort_method=sort_method_enum,
            diff=diff,
            git_diff_branch=git_diff_branch if git_diff_branch else None,
            git_log_branch=git_log_branch if git_log_branch else None,
            encoding=encoding,
            show_tokens_format=show_tokens_format_enum,
            output_file=output_file_str,
            clipboard=clipboard,
        )

    except (SmartPromptBuilderError, ConfigError) as e:  # Catch specific config errors
        logger.debug(
            "Configuration error details:", exc_info=True
        )  # Log traceback only in debug
        click.echo(f"Configuration Error: {e}", err=True)
        sys.exit(1)
    except click.ClickException as e:
        e.show()
        sys.exit(e.exit_code)
    except Exception as e:
        logger.exception("Unexpected error during setup")
        click.echo(f"An unexpected setup error occurred: {e}", err=True)
        sys.exit(1)

    # --- Run Generator ---
    try:
        logger.info("Initializing prompt generator...")
        generator = PromptGenerator(config)
        logger.info("Starting prompt generation...")
        final_prompt_content = generator.generate()  # This is the rendered content
        logger.info("Prompt generation complete.")

        # --- Handle Output ---
        is_json_output = config.output_format == OutputFormat.JSON
        output_content = ""

        if is_json_output:
            # Create the final JSON structure
            json_payload = {
                "prompt": final_prompt_content,
                "token_count": generator.token_count,
                "encoding": config.encoding,
                "config_summary": {
                    "base_dir": str(config.base_dir),
                    "num_files_included": len(generator.file_data),
                    "output_mode": "json",
                    "template_source": "custom"
                    if config.template_path
                    else (
                        config.preset_template.value
                        if config.preset_template
                        else config.output_format.value
                    ),
                    "sort_method": config.sort_method.value,
                    "git_diff_enabled": config.diff,
                    "git_diff_branch_enabled": bool(config.git_diff_branch),
                    "git_log_branch_enabled": bool(config.git_log_branch),
                },
            }
            # Add token count only if calculated
            if generator.token_count is None and config.show_tokens_format:
                logger.warning(
                    "Token count requested but not calculated (possibly due to error)."
                )
            elif generator.token_count is not None:
                json_payload["token_count"] = generator.token_count

            try:
                output_content = json.dumps(json_payload, indent=2)
            except TypeError as e:
                logger.error(f"Failed to serialize output to JSON: {e}")
                # Fallback to just dumping the prompt content? Or raise error?
                output_content = final_prompt_content  # Fallback to raw content
                is_json_output = False  # Treat as non-json output now
        else:
            output_content = final_prompt_content

        output_written_somewhere = False
        if config.output_file:
            write_to_file(config.output_file, output_content)
            click.echo(f"Output written to: {config.output_file}", err=True)
            output_written_somewhere = True

        if config.clipboard:
            # Always copy the raw prompt content, not the JSON structure
            copy_to_clipboard(final_prompt_content)
            output_written_somewhere = True  # Clipboard counts as output destination

        if not output_written_somewhere:
            # Default to stdout if no other output specified
            write_to_stdout(output_content)

        # Display token count to stderr if requested
        if generator.token_count is not None and config.show_tokens_format:
            count_str = str(generator.token_count)
            if config.show_tokens_format == TokenCountFormat.HUMAN:
                count_str = f"{generator.token_count:,}"  # Simple comma formatting
            click.echo(f"Token count ({config.encoding}): {count_str}", err=True)
        elif config.show_tokens_format and generator.token_count is None:
            logger.warning("Token count requested but could not be calculated.")
            click.echo(
                f"Token count ({config.encoding}): calculation failed.", err=True
            )

    except SmartPromptBuilderError as e:
        logger.debug("Generation or output error details:", exc_info=True)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error during generation/output")
        click.echo(f"An unexpected error occurred: {e}", err=True)
        sys.exit(1)

if __name__ == '__main__':
    main_cli(prog_name="llmfiles")  # Provide prog_name for standalone execution