# llmfiles/config.py
"""Configuration dataclasses and enums for llmfiles."""

import structlog
from dataclasses import (
    dataclass,
    field,
)  # Removed MISSING as it wasn't used in the final PromptConfig
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

# from llmfiles.exceptions import ConfigError # Not strictly needed if not raising ConfigError here

log = structlog.get_logger(__name__)


class SortMethod(Enum):
    """Defines available methods for sorting discovered files."""

    NAME_ASC, NAME_DESC, DATE_ASC, DATE_DESC = (
        "name_asc",
        "name_desc",
        "date_asc",
        "date_desc",
    )

    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["SortMethod"]:
        if not s:
            return None
        try:
            return cls(s.lower())
        except ValueError:
            log.warning("invalid_sort_method_string", input_string=s)
            return None


class OutputFormat(Enum):
    """Defines supported output formats for the generated prompt."""

    MARKDOWN, XML, JSON = "markdown", "xml", "json"

    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["OutputFormat"]:
        if not s:
            return None
        try:
            return cls(s.lower())
        except ValueError:
            log.warning("invalid_output_format_string", input_string=s)
            return None


class TokenCountFormat(Enum):
    """Defines display formats for token counts."""

    HUMAN, RAW = "human", "raw"

    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["TokenCountFormat"]:
        if not s:
            return None
        try:
            return cls(s.lower())
        except ValueError:
            log.warning("invalid_token_count_format_string", input_string=s)
            return None


class PresetTemplate(Enum):
    """Defines built-in template presets."""

    DEFAULT, CLAUDE_OPTIMAL, GENERIC_XML = "default", "claude-optimal", "generic-xml"

    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["PresetTemplate"]:
        if not s:
            return None
        try:
            return cls(s.lower())
        except ValueError:
            log.warning("invalid_preset_template_string", input_string=s)
            return None


# Default values for various configuration options
DEFAULT_YAML_TRUNCATION_PLACEHOLDER = "<content truncated due to length>"
DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN = 500
DEFAULT_CONSOLE_SHOW_TREE = True
DEFAULT_CONSOLE_SHOW_SUMMARY = True
DEFAULT_CONSOLE_SHOW_TOKEN_COUNT = False
DEFAULT_OUTPUT_FORMAT = OutputFormat.MARKDOWN
DEFAULT_SORT_METHOD = SortMethod.NAME_ASC
DEFAULT_ENCODING = "cl100k"


@dataclass
class PromptConfig:
    """Holds all configuration parameters for generating the prompt."""
    input_paths: List[Path] = field(default_factory=lambda: [Path(".")])
    read_from_stdin: bool = False
    nul_separated: bool = False  # For stdin processing
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    include_from_files: List[Path] = field(
        default_factory=list
    )  # Files containing include patterns
    exclude_from_files: List[Path] = field(
        default_factory=list
    )  # Files containing exclude patterns
    include_priority: bool = (
        False  # If true, include patterns override exclude patterns
    )
    no_ignore: bool = False  # Disables .gitignore file processing
    hidden: bool = False  # Includes hidden files and directories
    follow_symlinks: bool = False  # Follows symbolic links during discovery
    template_path: Optional[Path] = None  # Path to a custom handlebars template
    preset_template: Optional[PresetTemplate] = (
        None  # Name of a built-in template preset
    )
    user_vars: Dict[str, str] = field(
        default_factory=dict
    )  # User-defined variables for templates
    output_format: OutputFormat = (
        DEFAULT_OUTPUT_FORMAT  # Default output format if no template/preset
    )
    line_numbers: bool = False  # Prepend line numbers to file content
    no_codeblock: bool = False  # Omit markdown code blocks around file content
    absolute_paths: bool = False  # Use absolute paths for files in the output list
    show_absolute_project_path: bool = (
        False  # Show full absolute path for project root in header
    )
    process_yaml_truncate_long_fields: bool = (
        False  # Enable/disable truncation of long YAML fields
    )
    yaml_truncate_placeholder: str = DEFAULT_YAML_TRUNCATION_PLACEHOLDER
    yaml_truncate_content_max_len: int = DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN
    sort_method: SortMethod = DEFAULT_SORT_METHOD  # Method for sorting included files
    diff: bool = False  # Include staged git diff
    git_diff_branch: Optional[Tuple[str, str]] = (
        None  # (base, compare) for git diff between branches
    )
    git_log_branch: Optional[Tuple[str, str]] = (
        None  # (base, compare) for git log between branches
    )
    encoding: str = DEFAULT_ENCODING  # Tiktoken encoding for token counting
    show_tokens_format: Optional[TokenCountFormat] = (
        None  # Format for displaying token count on stderr (main output)
    )
    output_file: Optional[Path] = None  # Path to write the generated prompt to
    clipboard: bool = False  # Copy the generated prompt to the clipboard
    console_show_tree: bool = (
        DEFAULT_CONSOLE_SHOW_TREE  # Show directory tree on console
    )
    console_show_summary: bool = (
        DEFAULT_CONSOLE_SHOW_SUMMARY  # Show file count summary on console
    )
    console_show_token_count: bool = (
        DEFAULT_CONSOLE_SHOW_TOKEN_COUNT  # Show token count on console (stderr)
    )

    save_profile_name: Optional[str] = field(
        default=None, init=True
    )  # Profile name for --save feature

    # Internal state, not typically set by user through config files directly
    resolved_input_paths: List[Path] = field(
        default_factory=list, init=False
    )  # Stores resolved absolute seed paths
    base_dir: Path = field(
        init=False
    )  # Root directory for relative path calculations and discovery

    def __post_init__(self):
        """Performs initial setup after dataclass instantiation."""
        self.base_dir = Path.cwd().resolve()
        log.info("PromptConfig.base_dir_initialized", path=str(self.base_dir))

        if self.template_path and self.preset_template:
            log.warning(
                "custom_template_overrides_preset",
                custom_template=str(self.template_path),
                preset=self.preset_template.value if self.preset_template else "none",
            )

        # Corrected log key here from previous version in my head
        log.debug(
            "PromptConfig_finalized",
            show_absolute_project_path_setting=self.show_absolute_project_path,
        )