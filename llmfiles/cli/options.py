# llmfiles/cli/options.py
"""
Defines reusable groups of Click options for the llmfiles CLI.
Uses click_option_group for better help message formatting.
"""
import click
from click_option_group import optgroup
from pathlib import Path

# Import Enums and DEFAULTS from config.settings to use in help texts or defaults
from llmfiles.config.settings import (
    SortMethod, OutputFormat, TokenCountFormat, PresetTemplate, ChunkStrategy,
    DEFAULT_OUTPUT_FORMAT, DEFAULT_SORT_METHOD, DEFAULT_ENCODING,
    DEFAULT_YAML_TRUNCATION_PLACEHOLDER, DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN,
    DEFAULT_CHUNK_STRATEGY
)

# This file will contain functions that apply groups of options to a Click command/group.
# Each function will take a `cmd` (the Click command/group object) as an argument
# and apply decorators to it. This way, the main CLI interface file stays cleaner.

def add_input_options(cmd):
    """Applies input source and path options to a Click command."""
    @optgroup.group("Input Source Options", help="Configure where to get files and paths from.")
    @optgroup.option("-in", "-I", "--input-path", "input_paths", multiple=True, type=click.Path(path_type=Path), default=None, help="Paths to include (files or directories). Default: current directory '.' if not reading from stdin.")
    @optgroup.option("--stdin", "read_from_stdin", is_flag=True, default=False, help="Read paths from stdin, one path per line.")
    @optgroup.option("-0", "--null", "nul_separated", is_flag=True, default=False, help="Input paths from stdin are NUL-separated (for use with find ... -print0).")
    @cmd.command # This doesn't make sense here, optgroup.option are what we need on the main command
    def _input_options_group_decorator(): pass # Dummy for applying group
    # The above structure with @cmd.command is incorrect for click-option-group.
    # Options are directly applied to the main command. Let's redefine.
    # This file will export functions that RETURN lists of decorators,
    # or we apply directly in cli/interface.py.
    # For simplicity, let's define them as functions that apply decorators,
    # which means the main command must be passed around or options defined directly.
    # A simpler way: just define the options and group them in cli/interface.py.
    # This file `options.py` can then just be a manifest or conceptual grouping.

    # Let's re-think: click-option-group works by decorating the command directly.
    # So, these functions can't really "return" groups in a way that Click later applies them.
    # The groups and options must be defined where the command is defined.
    # This file can serve as a reference for which options belong to which conceptual group.

    # Therefore, this file might be less about functions returning decorators,
    # and more about defining the options lists conceptually, or it might not be needed
    # if cli/interface.py directly uses @optgroup.
    
    # For now, let's assume cli/interface.py will handle the direct decoration.
    # This file can be a placeholder or used if a more complex option sharing system is needed later.
    pass # Placeholder for now, actual decoration will be in cli/interface.py

# Conceptual grouping (actual decorators will be in cli/interface.py):

# Input Options:
#   -in, --input-path
#   --stdin
#   -0, --null

# Filtering Options:
#   -i, --include
#   -e, --exclude
#   --include-from-file
#   --exclude-from-file
#   --include-priority
#   --no-ignore
#   --hidden
#   -L, --follow-symlinks

# Content Processing & Chunking Options:
#   --chunk-strategy
#   --yaml-truncate-long-fields
#   --yaml-placeholder
#   --yaml-max-len

# Output Formatting & Templating Options:
#   -t, --template
#   --preset
#   --var
#   -F, --output-format
#   -n, --line-numbers
#   --no-codeblock
#   --absolute-paths (for file list in content)
#   --show-abs-project-path (for header)

# Git Integration Options:
#   --diff
#   --git-diff-branch
#   --git-log-branch

# Tokenizer & Output Options:
#   -c, --encoding
#   --show-tokens
#   -o, --output
#   --clipboard
#   --sort

# Console Feedback Options:
#   --console-tree / --no-console-tree
#   --console-summary / --no-console-summary
#   --console-tokens / --no-console-tokens

# Application Behavior Options:
#   --config-profile
#   --save
#   -v, --verbose
#   --force-json-logs
#   --version
#   -h, --help