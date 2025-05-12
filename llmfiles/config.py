# llmfiles/config.py
"""configuration dataclasses and enums for llmfiles."""

import structlog
from dataclasses import dataclass, field, fields as dataclass_fields, MISSING
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Set, Any

from llmfiles.exceptions import ConfigError

log = structlog.get_logger(__name__)

# --- enums for configuration choices ---
class SortMethod(Enum):
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
    # (all fields as previously defined)
    input_paths: List[Path] = field(default_factory=lambda: [Path(".")])
    read_from_stdin: bool = False
    nul_separated: bool = False
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    include_priority: bool = False
    no_ignore: bool = False
    hidden: bool = False
    follow_symlinks: bool = False
    template_path: Optional[Path] = None
    preset_template: Optional[PresetTemplate] = None
    user_vars: Dict[str, str] = field(default_factory=dict)
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT
    line_numbers: bool = False
    no_codeblock: bool = False
    absolute_paths: bool = False
    process_yaml_truncate_long_fields: bool = False
    yaml_truncate_placeholder: str = DEFAULT_YAML_TRUNCATION_PLACEHOLDER
    yaml_truncate_content_max_len: int = DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN
    sort_method: SortMethod = DEFAULT_SORT_METHOD
    diff: bool = False
    git_diff_branch: Optional[Tuple[str, str]] = None
    git_log_branch: Optional[Tuple[str, str]] = None
    encoding: str = DEFAULT_ENCODING
    show_tokens_format: Optional[TokenCountFormat] = None
    output_file: Optional[Path] = None
    clipboard: bool = False
    console_show_tree: bool = DEFAULT_CONSOLE_SHOW_TREE
    console_show_summary: bool = DEFAULT_CONSOLE_SHOW_SUMMARY
    console_show_token_count: bool = DEFAULT_CONSOLE_SHOW_TOKEN_COUNT
    resolved_input_paths: List[Path] = field(default_factory=list, init=False)
    base_dir: Path = field(
        init=False
    )  # this is the root for relative path calculations for filters

    def __post_init__(self):
        """
        finalizes `base_dir`. this directory is considered the "project root"
        for the purpose of applying glob patterns and discovering .gitignore files
        that are at this root or above it (up to a logical project boundary if discernible,
        or filesystem root in worst case for items outside typical project structure).
        """
        # `input_paths` are as provided by user (cli or config).
        # they might be relative or absolute.
        # `base_dir` should be an absolute path that serves as the reference
        # for all relative path operations for filtering (include, exclude, gitignore).

        # strategy:
        # 1. if input_paths are all absolute and share a common ancestor that is a git repo root,
        #    or some other project marker, that could be base_dir. (complex to implement robustly)
        # 2. simpler: use current working directory (cwd) as the primary `base_dir`.
        #    this means `.gitignore` in cwd and its parents will be considered broadly.
        #    include/exclude patterns are then relative to this `base_dir`.
        #    `resolved_input_paths` (from discovery) will be absolute.
        #    when checking an item, its path relative to this `base_dir` is used for filtering.

        self.base_dir = Path.cwd().resolve()
        log.info("base_dir_set_to_cwd_for_filtering_scope", path=str(self.base_dir))

        # `self.input_paths` (from user) are resolved to absolute paths later in `_determine_initial_seed_paths`.
        # `config.resolved_input_paths` will store these fully resolved, existing seed paths.

        if self.template_path and self.preset_template:
            log.warning(
                "custom_template_overrides_preset",
                custom_template=str(self.template_path),
                preset=self.preset_template.value if self.preset_template else "none",
            )

        log.debug("promptconfig_finalized_initial", base_dir=str(self.base_dir))