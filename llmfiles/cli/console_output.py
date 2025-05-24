# llmfiles/cli/console_output.py
"""
Handles printing summary information to the console (stderr) during CLI execution.
"""
import click
import structlog

# Assuming PromptConfig and PromptGenerator types are defined and will be passed
# For type hinting, we might need to import them, but they are passed as arguments.
# from llmfiles.config.settings import PromptConfig (if needed for internal type checks)
# from llmfiles.core.pipeline import PromptGenerator (if needed for internal type checks)
from llmfiles.core.templating import build_template_context # Needed for tree rendering

log = structlog.get_logger(__name__)

def print_cli_summary_output(config, generator): # Using generic types for now
    """
    Prints summary information like element counts and project tree to stderr.
    'config' is an instance of PromptConfig.
    'generator' is an instance of PromptGenerator.
    """
    log.debug("console_summary_output_requested")

    if config.console_show_summary:
        click.secho("--- Execution Summary ---", fg="cyan", err=True)
        unique_files_count = len(set(el.get("file_path") for el in generator.content_elements if el.get("file_path")))
        click.echo(
            f"Content elements generated: {len(generator.content_elements)} (from {unique_files_count} unique files)", 
            err=True
        )

    if config.console_show_tree and generator.content_elements:
        # build_template_context will use 'file_path' from elements to construct the tree
        tree_context_data = build_template_context(config, generator.content_elements, None, None, None)
        source_tree_str = tree_context_data.get("source_tree")
        if source_tree_str:
            click.secho("\n--- Project Structure (based on included content) ---", fg="cyan", err=True)
            click.echo(source_tree_str, err=True)
            
    if config.console_show_token_count:
        token_val = generator.token_count
        token_display_str = f"{token_val:,}" if token_val is not None and isinstance(token_val, int) else str(token_val or "N/A")
        click.secho(
            f"\nEstimated Token Count (encoding: {config.encoding}): {token_display_str}",
            fg="yellow",
            err=True
        )