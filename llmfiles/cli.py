# llmfiles/cli.py
"""Command Line Interface using Click."""
import click
import sys
import logging
import json
from pathlib import Path
from typing import List, Optional, Dict, Any

import tiktoken
from rich.progress import Progress, SpinnerColumn, TextColumn

# Relative imports for sibling modules
from .config import PromptConfig, SortMethod, OutputFormat, TokenCountFormat
from .discovery import discover_paths
from .processing import process_file_content
from .git_utils import get_diff, get_diff_branches, get_log_branches, check_is_git_repo
from .templating import TemplateRenderer, build_template_context
from .output import write_to_stdout, write_to_file, copy_to_clipboard
from .exceptions import SmartPromptBuilderError, TokenizerError

# Basic logging config if not set elsewhere
logging.basicConfig(level=logging.WARNING, format='%(levelname)s [%(name)s]: %(message)s')
logger = logging.getLogger(__name__)


# --- Generator Class ---
# This class encapsulates the main generation logic (enhanced approach 2)
class PromptGenerator:
    def __init__(self, config: PromptConfig):
        self.config = config
        self.file_data: List[Dict[str, Any]] = [] # Holds {path, relative_path, content, raw_content, mod_time}
        self.git_diff_data: Optional[str] = None
        self.git_diff_branches_data: Optional[str] = None
        self.git_log_branches_data: Optional[str] = None
        self.rendered_prompt: Optional[str] = None
        self.token_count: Optional[int] = None

    def _discover_and_filter_paths(self):
        """Generates filtered paths based on config."""
        logger.info("Discovering and filtering paths...")
        # The discover_paths function handles config flags like hidden, no_ignore, etc.
        yield from discover_paths(self.config)

    def _process_file_contents(self):
        """Reads and processes content for filtered files."""
        logger.info("Processing file contents...")
        temp_file_data = []
        # Use the generator from _discover_and_filter_paths
        for path in self._discover_and_filter_paths():
             processed_data = process_file_content(path, self.config)
             if processed_data:
                 content, raw_content, mod_time = processed_data
                 relative_path = path.relative_to(self.config.base_dir)
                 abs_path_str = str(path)
                 rel_path_str = str(relative_path)
                 file_entry = {
                     "path": abs_path_str if self.config.absolute_paths else rel_path_str,
                     "relative_path": rel_path_str, # Always provide relative path
                     "content": content,
                     "raw_content": raw_content,
                     "extension": path.suffix[1:].lower() if path.suffix else "",
                     "mod_time": mod_time
                 }
                 temp_file_data.append(file_entry)
        self.file_data = temp_file_data # Store after iterating generator

    def _sort_file_data(self):
         """Sorts the collected file data based on config."""
         logger.info(f"Sorting file data by {self.config.sort_method.value}...")
         sort_key = None
         reverse = False

         if self.config.sort_method == SortMethod.NAME_ASC:
             sort_key = lambda x: x['relative_path'] # Sort by relative path for consistency
         elif self.config.sort_method == SortMethod.NAME_DESC:
             sort_key = lambda x: x['relative_path']
             reverse = True
         elif self.config.sort_method == SortMethod.DATE_ASC:
             # Use float('inf') for files where mod_time couldn't be read
             sort_key = lambda x: x.get('mod_time') if x.get('mod_time') is not None else float('inf')
         elif self.config.sort_method == SortMethod.DATE_DESC:
             sort_key = lambda x: x.get('mod_time') if x.get('mod_time') is not None else float('-inf')
             reverse = True

         if sort_key:
             try:
                 self.file_data.sort(key=sort_key, reverse=reverse)
                 logger.debug("File data sorted.")
             except Exception as e:
                 logger.warning(f"Could not sort file data: {e}. Proceeding without sorting.")
         else:
             logger.debug("No sorting key defined, skipping sort.")


    def _fetch_git_info(self):
        """Fetches Git information if configured."""
        repo_path = self.config.base_dir
        is_repo = check_is_git_repo(repo_path)
        if not is_repo:
             if self.config.diff or self.config.git_diff_branch or self.config.git_log_branch:
                logger.warning(f"Path {repo_path} is not a git repository. Skipping git operations.")
             return # No need to proceed if not a repo

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
                enc = tiktoken.get_encoding(self.config.encoding)
                tokens = enc.encode(self.rendered_prompt)
                self.token_count = len(tokens)
                logger.info(f"Token count: {self.token_count}")
            except ValueError:
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
             task1 = progress.add_task("Discovering files...", total=None)
             self._process_file_contents() # Combines discovery and processing per file
             progress.update(task1, completed=1, description="Files discovered and processed.")

             task2 = progress.add_task("Sorting files...", total=None)
             self._sort_file_data()
             progress.update(task2, completed=1, description="File data sorted.")

             if self.config.diff or self.config.git_diff_branch or self.config.git_log_branch:
                 task3 = progress.add_task("Fetching Git info...", total=None)
                 self._fetch_git_info()
                 progress.update(task3, completed=1, description="Git info fetched.")

             task4 = progress.add_task("Rendering prompt...", total=None)
             self._render()
             progress.update(task4, completed=1, description="Prompt rendered.")

             if self.config.show_tokens_format:
                task5 = progress.add_task("Calculating tokens...", total=None)
                self._calculate_tokens()
                progress.update(task5, completed=1, description="Tokens calculated.")

         if self.rendered_prompt is None:
             raise SmartPromptBuilderError("Prompt generation failed to produce output.")
         return self.rendered_prompt


# --- Click CLI Definition ---
@click.command()
@click.argument('input_paths_arg', nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.option('--stdin', 'read_from_stdin', is_flag=True, help='Read list of paths from stdin.')
@click.option('-0', '--null', 'nul_separated', is_flag=True, help='Input paths from stdin are NUL-separated.')
@click.option('-i', '--include', 'include_patterns', multiple=True, help='Glob pattern(s) for files/dirs to include. Multiple allowed.')
@click.option('-e', '--exclude', 'exclude_patterns', multiple=True, help='Glob pattern(s) for files/dirs to exclude. Multiple allowed.')
@click.option('--include-priority', is_flag=True, default=False, help='Include files if matching both include and exclude patterns.')
@click.option('--no-ignore', is_flag=True, default=False, help='Disable .gitignore file processing.')
@click.option('--hidden', is_flag=True, default=False, help='Include hidden files and directories (starting with ".").')
@click.option('-L', '--follow-symlinks', is_flag=True, default=False, help='Follow symbolic links.')
@click.option('-t', '--template', 'template_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), help='Path to a custom Handlebars template file.')
@click.option('--var', 'user_vars_list', multiple=True, help='User variables for template (key=value).')
@click.option('-F', '--output-format', type=click.Choice([f.value for f in OutputFormat], case_sensitive=False), default=OutputFormat.MARKDOWN.value, help='Output format preset (markdown, xml, json). Overridden by --template.')
@click.option('-n', '--line-numbers', is_flag=True, default=False, help='Prepend line numbers to code content.')
@click.option('--no-codeblock', is_flag=True, default=False, help='Do not wrap code content in Markdown code blocks.')
@click.option('--absolute-paths', is_flag=True, default=False, help='Use absolute paths in output instead of relative.')
@click.option('--sort', 'sort_method_str', type=click.Choice([s.value for s in SortMethod], case_sensitive=False), default=SortMethod.NAME_ASC.value, help='Sort files by method.')
@click.option('--diff', is_flag=True, default=False, help='Include staged Git diff (HEAD vs Index).')
@click.option('--git-diff-branch', nargs=2, help='Include Git diff between two branches (e.g., main feature/branch).')
@click.option('--git-log-branch', nargs=2, help='Include Git log between two branches.')
@click.option('-c', '--encoding', default='cl100k', help='Tokenizer encoding for token count (e.g., cl100k, o200k, gpt2).')
@click.option('--show-tokens', 'show_tokens_format_str', type=click.Choice([f.value for f in TokenCountFormat], case_sensitive=False), help='Show token count on stderr (human or raw format).')
@click.option('-o', '--output', 'output_file_str', type=click.Path(dir_okay=False, writable=True, path_type=Path), help='Write output to file instead of stdout.')
@click.option('--clipboard', is_flag=True, default=False, help='Copy output to system clipboard.')
@click.option('--verbose', '-v', count=True, help='Increase verbosity (-v for INFO, -vv for DEBUG).')
@click.version_option(package_name='smart-prompt-builder') # Assumes package name matches

def main_cli(
    input_paths_arg: tuple[Path, ...],
    read_from_stdin: bool,
    nul_separated: bool,
    include_patterns: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
    include_priority: bool,
    no_ignore: bool,
    hidden: bool,
    follow_symlinks: bool,
    template_path: Optional[Path],
    user_vars_list: tuple[str, ...],
    output_format: str,
    line_numbers: bool,
    no_codeblock: bool,
    absolute_paths: bool,
    sort_method_str: str,
    diff: bool,
    git_diff_branch: Optional[tuple[str, str]],
    git_log_branch: Optional[tuple[str, str]],
    encoding: str,
    show_tokens_format_str: Optional[str],
    output_file_str: Optional[Path],
    clipboard: bool,
    verbose: int,
):
    """
    Smart Prompt Builder: Generate LLM prompts from codebases, git info, and templates.

    Provide file/directory paths as arguments or pipe them via stdin.
    """
    # --- Logging Setup ---
    log_level = logging.WARNING
    if verbose == 1:
        log_level = logging.INFO
    elif verbose >= 2:
        log_level = logging.DEBUG
    # Reconfigure root logger - adjust format as needed
    logging.basicConfig(level=log_level, format='%(levelname)-8s [%(name)s] %(message)s', force=True)
    logger.info(f"Log level set to {logging.getLevelName(log_level)}")


    # --- Input Validation and Config Building ---
    try:
        if not input_paths_arg and not read_from_stdin:
             raise click.UsageError("Must provide input paths or use --stdin.")
        if nul_separated and not read_from_stdin:
             raise click.UsageError("-0/--null requires --stdin.")

        # Parse user vars
        user_vars: Dict[str, str] = {}
        for var_item in user_vars_list:
            if '=' not in var_item:
                 raise click.BadParameter(f"Invalid format for --var '{var_item}'. Use key=value.", param_hint='--var')
            key, value = var_item.split('=', 1)
            user_vars[key.strip()] = value

        # Resolve enums
        output_format_enum = OutputFormat.from_string(output_format)
        sort_method_enum = SortMethod.from_string(sort_method_str)
        show_tokens_format_enum = TokenCountFormat.from_string(show_tokens_format_str) if show_tokens_format_str else None

        if not output_format_enum: raise click.BadParameter(f"Invalid output format: {output_format}", param_hint='--output-format')
        if not sort_method_enum: raise click.BadParameter(f"Invalid sort method: {sort_method_str}", param_hint='--sort')
        if show_tokens_format_str and not show_tokens_format_enum: raise click.BadParameter(f"Invalid token format: {show_tokens_format_str}", param_hint='--show-tokens')

        # Build Config object
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

    except SmartPromptBuilderError as e:
        logger.exception("Configuration error") # Log traceback if debug
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except click.ClickException as e:
         e.show()
         sys.exit(e.exit_code)
    except Exception as e:
         logger.exception("Unexpected error during setup")
         click.echo(f"An unexpected error occurred: {e}", err=True)
         sys.exit(1)


    # --- Run Generator ---
    try:
        logger.info("Initializing prompt generator...")
        generator = PromptGenerator(config)
        logger.info("Starting prompt generation...")
        final_prompt = generator.generate()
        logger.info("Prompt generation complete.")

        # --- Handle Output ---
        is_json_output = config.output_format == OutputFormat.JSON

        # Prepare JSON payload if needed BEFORE potentially copying/writing non-JSON
        json_output_payload = None
        if is_json_output:
             json_output_payload = json.dumps({
                 "prompt": final_prompt, # The rendered content (often Markdown or XML based on template)
                 "token_count": generator.token_count,
                 "encoding": config.encoding,
                 "config_summary": { # Add key config options for context
                      "base_dir": str(config.base_dir),
                      "num_files_included": len(generator.file_data),
                      "output_format": config.output_format.value,
                      "sort_method": config.sort_method.value,
                      "git_diff_enabled": config.diff,
                      # etc.
                 }
             }, indent=2)


        output_written = False
        if config.output_file:
            write_to_file(config.output_file, json_output_payload or final_prompt)
            click.echo(f"Output written to: {config.output_file}", err=True)
            output_written = True

        if config.clipboard:
            # Don't copy the JSON structure, copy the underlying prompt content
            copy_to_clipboard(final_prompt)
            # copy_to_clipboard already prints status to stderr
            output_written = True # Consider clipboard as "output written"

        if not output_written:
            # Default to stdout if no other output specified
            write_to_stdout(json_output_payload or final_prompt)


        # Display token count if requested
        if generator.token_count is not None and config.show_tokens_format:
            count_str = str(generator.token_count)
            if config.show_tokens_format == TokenCountFormat.HUMAN:
                 # Simple comma formatting
                 count_str = f"{generator.token_count:,}"
            click.echo(f"Token count ({config.encoding}): {count_str}", err=True)

    except SmartPromptBuilderError as e:
        logger.exception("Generation or output error")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error during generation/output")
        click.echo(f"An unexpected error occurred: {e}", err=True)
        sys.exit(1)

if __name__ == '__main__':
    main_cli()