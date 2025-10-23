# llmfiles/cli/interface.py
import sys
from pathlib import Path
from typing import List
import click
import structlog

from llmfiles import __version__ as app_version
from llmfiles.config.settings import PromptConfig, ChunkStrategy, ExternalDepsStrategy
from llmfiles.logging_setup import configure_logging
from llmfiles.core.pipeline import PromptGenerator
from llmfiles.core.output import write_to_file, write_to_stdout
from llmfiles.exceptions import SmartPromptBuilderError
from llmfiles.structured_processing import ast_utils

log = structlog.get_logger(__name__)

def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}TB"

def _parse_file_size(size_str: str) -> int:
    """Parse human-readable file size to bytes (e.g., '1MB' -> 1048576)."""
    size_str = size_str.strip().upper()

    # Extract number and unit
    import re
    match = re.match(r'^(\d+(?:\.\d+)?)\s*([KMGT]?B?)$', size_str)
    if not match:
        raise ValueError(f"Invalid size format: {size_str}. Use formats like '1MB', '500KB', '10GB'")

    number = float(match.group(1))
    unit = match.group(2) or 'B'

    # Normalize unit (add B if missing)
    if unit in ['K', 'M', 'G', 'T']:
        unit = unit + 'B'

    multipliers = {
        'B': 1,
        'KB': 1024,
        'MB': 1024 ** 2,
        'GB': 1024 ** 3,
        'TB': 1024 ** 4
    }

    if unit not in multipliers:
        raise ValueError(f"Unknown size unit: {unit}")

    return int(number * multipliers[unit])

def _print_summary_to_console(included_files: List[dict]):
    # this helper function now lives in the cli module.
    if not included_files:
        click.secho("\n--- Summary ---", fg="yellow", err=True)
        click.secho("no files were included in the output based on the provided patterns.", fg="yellow", err=True)
        click.secho("---------------", fg="yellow", err=True)
        return

    click.secho(f"\n--- Included Files ({len(included_files)}) ---", fg="cyan", err=True)
    for file_info in included_files:
        file_path = file_info["path"]
        size_bytes = file_info.get("size_bytes", 0)
        size_str = _format_file_size(size_bytes)
        click.echo(f"  - {file_path} ({size_str})", err=True)
    click.secho("--------------------------\n", fg="cyan", err=True)

@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.argument("paths", nargs=-1, type=click.Path(path_type=Path))
@click.option(
    "-i", "--include", "include_patterns",
    multiple=True,
    help="glob pattern for files to include. can be used multiple times."
)
@click.option(
    "-e", "--exclude", "exclude_patterns",
    multiple=True,
    help="glob pattern for files to exclude. can be used multiple times."
)
@click.option(
    "--grep-content", "grep_content_pattern",
    type=str,
    default=None,
    help="search file contents for a pattern and include matching files as seeds for dependency resolution."
)
@click.option(
    "--chunk-strategy",
    type=click.Choice([cs.value for cs in ChunkStrategy]),
    default=ChunkStrategy.STRUCTURE.value,
    help="strategy for chunking files. 'structure' (default) uses ast parsing for supported languages. 'file' treats each file as a single chunk."
)
@click.option(
    "--external-deps", "external_deps_strategy",
    type=click.Choice([es.value for es in ExternalDepsStrategy]),
    default=ExternalDepsStrategy.IGNORE.value,
    help="strategy for handling external dependencies: 'ignore' or 'metadata'."
)
@click.option(
    "--no-ignore",
    is_flag=True,
    default=False,
    help="do not respect .gitignore files."
)
@click.option(
    "--hidden",
    is_flag=True,
    default=False,
    help="include hidden files and directories (starting with a dot)."
)
@click.option(
    "--include-binary",
    is_flag=True,
    default=False,
    help="include binary files (detected by UTF-8 decode errors). by default, binary files are excluded."
)
@click.option(
    "--max-size",
    type=str,
    default=None,
    help="exclude files larger than specified size (e.g., '1MB', '500KB', '10MB'). accepts units: B, KB, MB, GB."
)
@click.option(
    "-l", "--follow-symlinks",
    is_flag=True,
    default=False,
    help="follow symbolic links."
)
@click.option(
    "-n", "--line-numbers",
    is_flag=True,
    default=False,
    help="prepend line numbers to file content."
)
@click.option(
    "--no-codeblock",
    is_flag=True,
    default=False,
    help="omit markdown code blocks around file content."
)
@click.option(
    "-o", "--output", "output_file",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="write output to file instead of stdout."
)
@click.option(
    "--stdin", "read_from_stdin",
    is_flag=True,
    default=False,
    help="read file paths from standard input."
)
@click.option(
    "-0", "--null", "nul_separated",
    is_flag=True,
    default=False,
    help="when using --stdin, paths are separated by a nul character."
)
@click.option(
    "-r", "--recursive",
    is_flag=True,
    default=False,
    help="recursively include all local code imported by the seed files."
)
@click.option(
    "-v", "--verbose",
    is_flag=True,
    default=False,
    help="enable verbose logging output to stderr."
)
@click.version_option(version=app_version, package_name="llmfiles", prog_name="llmfiles")
def main_cli_group(paths, verbose, **kwargs):
    log_level = "info" if verbose else "warning"
    configure_logging(log_level_str=log_level)

    log.debug("cli_command_invoked", raw_args=kwargs)

    try:
        ast_utils.load_language_configs_for_llmfiles()

        kwargs["include_patterns"] = list(kwargs["include_patterns"])
        kwargs["exclude_patterns"] = list(kwargs["exclude_patterns"])

        # Convert include_binary flag to exclude_binary config
        kwargs["exclude_binary"] = not kwargs.pop("include_binary", False)

        # Parse max_size if provided
        max_size_str = kwargs.pop("max_size", None)
        if max_size_str:
            try:
                kwargs["max_file_size"] = _parse_file_size(max_size_str)
                log.info("max_file_size_set", size_bytes=kwargs["max_file_size"], size_str=max_size_str)
            except ValueError as e:
                click.secho(f"error: {e}", fg="red", err=True)
                sys.exit(1)
        else:
            kwargs["max_file_size"] = None

        kwargs["chunk_strategy"] = ChunkStrategy.from_string(kwargs["chunk_strategy"])
        kwargs["external_deps_strategy"] = ExternalDepsStrategy.from_string(kwargs["external_deps_strategy"])
        kwargs["input_paths"] = list(paths)

        config = PromptConfig(**kwargs)

        #
        # >>> THE FIX IS HERE <<<
        # the cli function now controls the flow of data and console output.
        #
        generator = PromptGenerator(config)

        # 1. generate the data. the progress bar runs inside this function.
        final_prompt, included_files = generator.generate()

        # 2. after the generator is finished, print the summary to stderr.
        _print_summary_to_console(included_files)

        # 3. handle the main output (to stdout or file).
        if final_prompt:
            if config.output_file:
                write_to_file(config.output_file, final_prompt)
                log.info("output_written_to_file", path=str(config.output_file))
            else:
                write_to_stdout(final_prompt)

    except SmartPromptBuilderError as e:
        log.error("application_error", message=str(e))
        click.secho(f"error: {e}", fg="red", err=True)
        sys.exit(1)
    except Exception as e:
        log.critical("unexpected_critical_error", message=str(e), exc_info=True)
        click.secho(f"unexpected critical error: {e}. please report this.", fg="red", err=True)
        sys.exit(1)
