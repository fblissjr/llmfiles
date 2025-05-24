# llmfiles/cli/interface.py
import sys
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, cast, Tuple
from dataclasses import fields as dataclass_fields, MISSING
from enum import Enum

import click
from click_option_group import optgroup
from rich.console import Console as RichConsole 
import structlog
import logging as stdlib_logging

from llmfiles import __version__ as app_version
from llmfiles.config.settings import (
    PromptConfig, SortMethod, OutputFormat, TokenCountFormat, PresetTemplate, ChunkStrategy,
    DEFAULT_CONSOLE_SHOW_TREE, DEFAULT_CONSOLE_SHOW_SUMMARY,
    DEFAULT_CONSOLE_SHOW_TOKEN_COUNT, DEFAULT_OUTPUT_FORMAT,
    DEFAULT_SORT_METHOD, DEFAULT_ENCODING, DEFAULT_CHUNK_STRATEGY
)
from llmfiles.config.loader import (
    load_and_merge_configs, save_config_to_profile, 
    CONFIG_KEY_TO_PROMPTCONFIG_ATTR_MAP
)
from llmfiles.logging_setup import configure_logging
from llmfiles.core.output import write_to_stdout, write_to_file, copy_to_clipboard
from llmfiles.core.pipeline import PromptGenerator
from llmfiles.exceptions import SmartPromptBuilderError, TokenizerError, ConfigError # TokenizerError might not be raised here directly
from llmfiles.structured_processing import ast_utils 

log = structlog.get_logger(__name__)

def _print_cli_summary_output(config: PromptConfig, generator: PromptGenerator):
    if config.console_show_summary:
        click.secho("--- execution summary ---", fg="cyan", err=True)
        unique_files = len(set(el.get("file_path") for el in generator.content_elements if el.get("file_path")))
        click.echo(f"Content elements generated: {len(generator.content_elements)} (from {unique_files} unique files)", err=True)
    if config.console_show_tree and generator.content_elements:
        from llmfiles.core.templating import build_template_context 
        tree_ctx = build_template_context(config, generator.content_elements, None, None, None)
        if tree_ctx.get("source_tree"):
            click.secho("\n--- project structure (based on included elements) ---", fg="cyan", err=True)
            click.echo(tree_ctx["source_tree"], err=True)
    if config.console_show_token_count and generator.token_count is not None:
        tc_val = generator.token_count
        token_display = f"{tc_val:,}" if isinstance(tc_val, int) else str(tc_val)
        click.secho(f"\nEstimated token count ({config.encoding}): {token_display}", fg="yellow", err=True)
    elif config.console_show_token_count:
         click.secho(f"\nEstimated token count ({config.encoding}): N/A", fg="yellow", err=True)

def _run_prompt_generation_flow(effective_config: PromptConfig):
    log.info("prompt_generation_orchestration_started", config_class=type(effective_config).__name__)
    generator = PromptGenerator(effective_config)
    final_prompt_str = generator.generate()
    log.info("prompt_generation_pipeline_complete_in_cli")

    output_to_write = final_prompt_str
    is_json_output_mode = effective_config.output_format == OutputFormat.JSON

    if is_json_output_mode:
        output_fmt_val = effective_config.output_format.value if effective_config.output_format else "unknown"
        tmpl_src_id = "default_for_format"
        if effective_config.template_path: tmpl_src_id = str(effective_config.template_path)
        elif effective_config.preset_template: tmpl_src_id = effective_config.preset_template.value
        
        json_payload: Dict[str, Any] = {
            "prompt_content": final_prompt_str,
            "metadata": {
                "base_directory": str(effective_config.base_dir),
                "elements_count": len(generator.content_elements), 
                "unique_files_in_elements": len(set(el.get("file_path") for el in generator.content_elements if el.get("file_path"))),
                "output_format_requested": output_fmt_val,
                "template_source_identifier": tmpl_src_id,
                "chunk_strategy_used": effective_config.chunk_strategy.value,
            },
        }
        if generator.token_count is not None:
            json_payload["token_information"] = {"count": generator.token_count, "encoding_used": effective_config.encoding}
        try: output_to_write = json.dumps(json_payload, indent=2) + "\n"
        except TypeError as e:
            log.error("json_serialization_failed_in_cli", error=str(e), exc_info=True)
            click.echo("Error: Failed to create JSON output. Falling back to raw prompt content.", err=True)
            is_json_output_mode = False 
            output_to_write = final_prompt_str 
            
    output_destination_used = False
    if effective_config.output_file:
        write_to_file(effective_config.output_file, output_to_write)
        click.echo(f"Info: Output written to: {effective_config.output_file}", err=True)
        output_destination_used = True
    
    clipboard_copy_succeeded = False
    if effective_config.clipboard:
        content_for_clipboard = output_to_write 
        if copy_to_clipboard(content_for_clipboard.strip()): clipboard_copy_succeeded = True
        output_destination_used = True

    if not output_destination_used or (effective_config.clipboard and not clipboard_copy_succeeded):
        if effective_config.clipboard and not clipboard_copy_succeeded:
            click.echo("Info: Clipboard copy failed. Outputting to stdout instead.", err=True)
        log.info("writing_final_output_to_stdout")
        write_to_stdout(output_to_write)

    if effective_config.show_tokens_format and generator.token_count is not None:
        fmt = effective_config.show_tokens_format
        count_str = f"{generator.token_count:,}" if fmt == TokenCountFormat.HUMAN else str(generator.token_count)
        click.echo(f"Token count (for main output, enc: '{effective_config.encoding}'): {count_str}", err=True)
    elif effective_config.show_tokens_format:
        log.warning("show_tokens_requested_but_not_available_in_cli")
        click.echo(f"Token count (for main output, enc: '{effective_config.encoding}'): N/A", err=True)

    _print_cli_summary_output(effective_config, generator)


@click.group(context_settings=dict(help_option_names=["-h", "--help"]), invoke_without_command=True)
@optgroup.group("Input Source Options", help="Configure where to get files and paths from.")
@optgroup.option("-in", "-I", "--input-path", "input_paths", multiple=True, type=click.Path(path_type=Path), default=None, help="Paths to include. Default: current directory '.' if not reading from stdin.")
@optgroup.option("--stdin", "read_from_stdin", is_flag=True, default=False, help="Read paths from stdin.")
@optgroup.option("-0", "--null", "nul_separated", is_flag=True, default=False, help="Input paths from stdin are NUL-separated.")
@optgroup.group("Filtering Options", help="Control which files and directories are processed.")
@optgroup.option("-i", "--include", "include_patterns", multiple=True, help="Glob patterns for files/directories to include.")
@optgroup.option("-e", "--exclude", "exclude_patterns", multiple=True, help="Glob patterns for files/directories to exclude.")
@optgroup.option("--include-from-file", "include_from_files", type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path, resolve_path=True), multiple=True, help="File(s) with include glob patterns.")
@optgroup.option("--exclude-from-file", "exclude_from_files", type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path, resolve_path=True), multiple=True, help="File(s) with exclude glob patterns.")
@optgroup.option("--include-priority", "include_priority", is_flag=True, default=False, help="Include patterns override exclude patterns.")
@optgroup.option("--no-ignore", "no_ignore", is_flag=True, default=False, help="Disable .gitignore file processing.")
@optgroup.option("--hidden", "hidden", is_flag=True, default=False, help="Include hidden files and directories.")
@optgroup.option("-L", "--follow-symlinks", "follow_symlinks", is_flag=True, default=False, help="Follow symbolic links.")
@optgroup.group("Content Processing Options", help="How content is processed and chunked.")
@optgroup.option("--chunk-strategy", "chunk_strategy_str", type=click.Choice([cs.value for cs in ChunkStrategy]), default=DEFAULT_CHUNK_STRATEGY.value, help=f"Strategy for chunking files. Default: {DEFAULT_CHUNK_STRATEGY.value}.")
@optgroup.group("Output Formatting & Templating", help="Control the appearance and structure of the output.")
@optgroup.option("-t", "--template", "template_path", type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path), default=None, help="Path to a custom Handlebars template file.")
@optgroup.option("--preset", "preset_template_str", type=click.Choice([p.value for p in PresetTemplate]), default=None, help="Use a built-in preset template.")
@optgroup.option("--var", "user_vars", multiple=True, metavar="KEY=VALUE", help="User-defined variables for templates.")
@optgroup.option("-F", "--output-format", "output_format_str", type=click.Choice([f.value for f in OutputFormat]), default=None, help=f"Output format if no template/preset. Default: {DEFAULT_OUTPUT_FORMAT.value}.")
@optgroup.option("-n", "--line-numbers", "line_numbers", is_flag=True, default=False, help="Prepend line numbers to file/chunk content.")
@optgroup.option("--no-codeblock", "no_codeblock", is_flag=True, default=False, help="Omit Markdown code blocks around content.")
@optgroup.option("--absolute-paths", "absolute_paths", is_flag=True, default=False, help="Use absolute paths for elements in the output list.")
@optgroup.option("--show-abs-project-path", "show_absolute_project_path", is_flag=True, default=False, help="Show full absolute path of project root in the output header.")
@optgroup.group("Git Integration", help="Options for including Git information.")
@optgroup.option("--diff", "diff", is_flag=True, default=False, help="Include staged git diff.")
@optgroup.option("--git-diff-branch", "git_diff_branch", nargs=2, metavar="BASE COMPARE", help="Git diff between two branches/commits.")
@optgroup.option("--git-log-branch", "git_log_branch", nargs=2, metavar="BASE COMPARE", help="Git log (commits in COMPARE not in BASE).")
@optgroup.group("Tokenization & Output Control", help="Token counting and general output settings.")
@optgroup.option("-c", "--encoding", "encoding", default=DEFAULT_ENCODING, help=f"Tiktoken encoding. Default: {DEFAULT_ENCODING}.")
@optgroup.option("--show-tokens", "show_tokens_format_str", type=click.Choice([f.value for f in TokenCountFormat]), default=None, help="Show token count (main output) on stderr.")
@optgroup.option("-o", "--output", "output_file", type=click.Path(dir_okay=False, writable=True, path_type=Path), default=None, help="Path to write the output to.")
@optgroup.option("--clipboard", "clipboard", is_flag=True, default=False, help="Copy output to clipboard.")
@optgroup.option("--sort", "sort_method_str", type=click.Choice([s.value for s in SortMethod]), default=None, help=f"Sort elements. Default: {DEFAULT_SORT_METHOD.value}.")
@optgroup.group("Console Feedback", help="Customize terminal output during execution (stderr).")
@optgroup.option("--console-tree/--no-console-tree", "console_show_tree", default=None, help=f"Show project structure tree. Default: {'on' if DEFAULT_CONSOLE_SHOW_TREE else 'off'}.") # Help reflects actual default
@optgroup.option("--console-summary/--no-console-summary", "console_show_summary", default=None, help=f"Show file/element count summary. Default: {'on' if DEFAULT_CONSOLE_SHOW_SUMMARY else 'off'}.")
@optgroup.option("--console-tokens/--no-console-tokens", "console_show_token_count", default=None, help=f"Show token count on console. Default: {'on' if DEFAULT_CONSOLE_SHOW_TOKEN_COUNT else 'off'}.")
@optgroup.group("Application Behavior", help="Configuration profiles, saving, and logging.")
@optgroup.option("--config-profile", "active_config_profile_name", default=None, help="Load a profile from config file(s).")
@optgroup.option("--save", "save_profile_name", type=str, metavar="PROFILE_NAME", default=None, help="Save options to a profile in project's .llmfiles.toml. Exits after saving.")
@optgroup.option("--verbose", "-v", "verbosity_level", count=True, help="Verbosity: -v info, -vv debug.")
@optgroup.option("--force-json-logs", "force_json_logs_cli", is_flag=True, default=False, help="Force JSON logs.")
@click.version_option(version=app_version, package_name="llmfiles", prog_name="llmfiles", help="Show version and exit.")
@click.help_option("-h", "--help", help="Show this message and exit.")
@click.pass_context
def main_cli_group(ctx: click.Context, **cli_params: Any):
    """llmfiles: Build LLM prompts from files, codebases, and git context
    using structured chunking and customizable templated outputs."""
    
    log_level = "warning"
    if cli_params.get("verbosity_level", 0) == 1: log_level = "info"
    elif cli_params.get("verbosity_level", 0) >= 2: log_level = "debug"
    configure_logging(log_level_str=log_level, force_json_logs=cli_params.get("force_json_logs_cli", False))

    log.debug("cli_command_invoked", params={k:v for k,v in cli_params.items() if k != 'save_profile_name' or v is not None})

    try:
        ##Initialize Tree-sitter languages ONCE ##
        log.debug("tree-sitter load_language_configs_for_llmfiles initializing...")
        ast_utils.load_language_configs_for_llmfiles()
        ## done init'ing tree-sitter ## 

        raw_configs_from_toml_files = load_and_merge_configs()
        active_profile_name = cli_params.get("active_config_profile_name")
        effective_options: Dict[str, Any] = {}

        for fd_init in dataclass_fields(PromptConfig):
            if fd_init.init:
                effective_options[fd_init.name] = fd_init.default_factory() if fd_init.default_factory is not MISSING else fd_init.default
        
        for toml_k, pc_attr in CONFIG_KEY_TO_PROMPTCONFIG_ATTR_MAP.items():
            if toml_k in raw_configs_from_toml_files:
                effective_options[pc_attr] = raw_configs_from_toml_files[toml_k]
        
        if active_profile_name:
            profile_values_toml = raw_configs_from_toml_files.get("profiles", {}).get(active_profile_name, {})
            if profile_values_toml:
                log.info("applying_profile_settings", profile=active_profile_name)
                for toml_k, pc_attr in CONFIG_KEY_TO_PROMPTCONFIG_ATTR_MAP.items():
                    if toml_k in profile_values_toml:
                        effective_options[pc_attr] = profile_values_toml[toml_k]
                if 'user_vars' in profile_values_toml and isinstance(profile_values_toml['user_vars'], dict): # Renamed TOML key
                    effective_options['user_vars'] = profile_values_toml['user_vars']
            else:
                log.warning("profile_not_found_in_config_files", profile_name=active_profile_name)

        # Layer CLI options, converting string enum values from CLI Choices
        enum_cli_args_map = { # Maps CLI string arg names to their corresponding Enum and PromptConfig attr name
            "chunk_strategy_str": (ChunkStrategy, "chunk_strategy"),
            "preset_template_str": (PresetTemplate, "preset_template"),
            "output_format_str": (OutputFormat, "output_format"),
            "show_tokens_format_str": (TokenCountFormat, "show_tokens_format"),
            "sort_method_str": (SortMethod, "sort_method"),
        }
        for pc_attr_name in {f.name for f in dataclass_fields(PromptConfig) if f.init}:
            if ctx.get_parameter_source(pc_attr_name) == click.core.ParameterSource.COMMANDLINE \
               or (pc_attr_name.endswith("_str") and ctx.get_parameter_source(pc_attr_name) == click.core.ParameterSource.COMMANDLINE): # For string versions of enums
                
                cli_key_to_check = pc_attr_name
                target_attr_name = pc_attr_name

                # Handle string versions of enums from CLI
                if pc_attr_name in enum_cli_args_map: # e.g. pc_attr_name is "chunk_strategy_str"
                    enum_cls, real_attr_name_for_pc = enum_cli_args_map[pc_attr_name]
                    cli_value_str = cli_params[pc_attr_name] # This is the string from Choice
                    if cli_value_str is not None:
                        parsed_enum = enum_cls.from_string(cli_value_str)
                        if parsed_enum: effective_options[real_attr_name_for_pc] = parsed_enum
                        # Else: Click's Choice should ensure it's a valid string, or from_string handles warning
                    target_attr_name = real_attr_name_for_pc # Ensure we are setting the correct PromptConfig attribute
                
                elif pc_attr_name == "user_vars": # Is a multiple=True option, gives tuple of "k=v"
                    cli_value_tuple = cli_params[pc_attr_name]
                    if cli_value_tuple: # It's a tuple of strings
                        effective_options[pc_attr_name] = {k.strip(): v for k,v in (s.split("=",1) for s in cli_value_tuple if "=" in s)}
                else: # Direct assignment for other types already handled by Click (Path, bool, int, list of Paths)
                    effective_options[pc_attr_name] = cli_params[pc_attr_name]
        
        # Type Coercion for paths and enums again, this time for values from TOML/profile that might be strings
        for pc_attr_name in list(effective_options.keys()):
            val = effective_options[pc_attr_name]
            if pc_attr_name in ("input_paths", "include_from_files", "exclude_from_files") and isinstance(val, list):
                effective_options[pc_attr_name] = [Path(p) for p in val if isinstance(p, str)]
            elif pc_attr_name in ("template_path", "output_file") and isinstance(val, str):
                effective_options[pc_attr_name] = Path(val) if val else None
            elif pc_attr_name in enum_cli_args_map.values() and isinstance(val, str): # Check against real attr names
                mapped_enum_cls = next((cls for k_str, (cls,attr) in enum_cli_args_map.items() if attr == pc_attr_name), None)
                if mapped_enum_cls:
                    parsed_enum = mapped_enum_cls.from_string(val) # type: ignore
                    if parsed_enum: effective_options[pc_attr_name] = parsed_enum
                    else: # Invalid string from TOML, fall back to dataclass default
                        df_field = next((f for f in dataclass_fields(PromptConfig) if f.name == pc_attr_name), None)
                        if df_field: effective_options[pc_attr_name] = df_field.default_factory() if df_field.default_factory is not MISSING else df_field.default
            # Default None from Click flags like console_* should be handled to respect dataclass defaults
            elif pc_attr_name in ("console_show_tree", "console_show_summary", "console_show_token_count") and val is None:
                df_field = next((f for f in dataclass_fields(PromptConfig) if f.name == pc_attr_name), None)
                if df_field: effective_options[pc_attr_name] = df_field.default

        valid_pc_fields = {f.name for f in dataclass_fields(PromptConfig) if f.init}
        final_pc_kwargs = {k: v for k, v in effective_options.items() if k in valid_pc_fields}
        for fd_final in dataclass_fields(PromptConfig): # Ensure all fields are present
            if fd_final.init and fd_final.name not in final_pc_kwargs:
                 final_pc_kwargs[fd_final.name] = fd_final.default_factory() if fd_final.default_factory is not MISSING else fd_final.default

        final_config = PromptConfig(**final_pc_kwargs)
        
        if final_config.save_profile_name:
            save_config_to_profile(final_config, final_config.save_profile_name)
            ctx.exit(0) 

        _run_prompt_generation_flow(final_config)

    except click.exceptions.Exit as e: raise e 
    except (ConfigError, SmartPromptBuilderError) as e: 
        log.error("handled_application_error_in_cli", error_type=type(e).__name__, message=str(e))
        click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)
    except click.ClickException as e: 
        log.error("click_exception_in_cli", error_type=type(e).__name__, message=str(e))
        e.show(); sys.exit(e.exit_code)
    except Exception as e: 
        log.critical("unexpected_critical_error_in_cli", message=str(e), exc_info=True)
        click.secho(f"Unexpected critical error: {e}. Please report this.", fg="red", err=True)
        sys.exit(1)