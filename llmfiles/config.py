# llmfiles/config.py
"""configuration dataclasses and enums for llmfiles."""

import structlog  # using structlog
from dataclasses import (
    dataclass,
    field,
    fields,
)  # import fields for default value iteration
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Set, Any

from llmfiles.exceptions import ConfigError
# from .config_file import get_merged_config_defaults # this will be called by cli.py

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
    def from_string(
        cls, s: Optional[str]
    ) -> Optional["SortMethod"]:  # allow s to be None
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

# --- hardcoded default values for various settings ---
# these are used if not specified by cli or config file.
# these also inform the cli help text for defaults.
DEFAULT_YAML_TRUNCATION_PLACEHOLDER = "<content truncated due to length>"
DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN = 500
DEFAULT_CONSOLE_SHOW_TREE = True
DEFAULT_CONSOLE_SHOW_SUMMARY = True
DEFAULT_CONSOLE_SHOW_TOKEN_COUNT = (
    False  # usually, token count is for the final prompt, not console
)
DEFAULT_OUTPUT_FORMAT = OutputFormat.MARKDOWN
DEFAULT_SORT_METHOD = SortMethod.NAME_ASC
DEFAULT_ENCODING = "cl100k"

@dataclass
class PromptConfig:
    """
    holds all resolved configuration settings for a single llmfiles run.
    values are derived from hardcoded defaults, config files, and cli arguments,
    with cli arguments having the highest precedence.
    """

    # input sources
    input_paths: List[Path] = field(
        default_factory=lambda: [Path(".")]
    )  # default to current dir if no paths given
    read_from_stdin: bool = False
    nul_separated: bool = False  # for stdin processing
    # filtering
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    include_priority: bool = False  # if true, include overrides exclude
    no_ignore: bool = False  # if true, do not use .gitignore
    hidden: bool = False  # if true, include hidden files/dirs
    follow_symlinks: bool = False
    # templating & content formatting
    template_path: Optional[Path] = None
    preset_template: Optional[PresetTemplate] = None
    user_vars: Dict[str, str] = field(default_factory=dict)
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT
    line_numbers: bool = False
    no_codeblock: bool = False  # if true, don't wrap in markdown code blocks
    absolute_paths: bool = False  # use absolute paths in output context
    # yaml specific processing
    process_yaml_truncate_long_fields: bool = False
    yaml_truncate_placeholder: str = DEFAULT_YAML_TRUNCATION_PLACEHOLDER
    yaml_truncate_content_max_len: int = DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN
    # sorting
    sort_method: SortMethod = DEFAULT_SORT_METHOD
    # git integration
    diff: bool = False  # include staged git diff
    git_diff_branch: Optional[Tuple[str, str]] = None
    git_log_branch: Optional[Tuple[str, str]] = None
    # token counting
    encoding: str = DEFAULT_ENCODING
    show_tokens_format: Optional[TokenCountFormat] = (
        None  # for json output or explicit stderr log
    )
    # output destinations
    output_file: Optional[Path] = None
    clipboard: bool = False
    # console output preferences (new)
    console_show_tree: bool = DEFAULT_CONSOLE_SHOW_TREE
    console_show_summary: bool = DEFAULT_CONSOLE_SHOW_SUMMARY
    console_show_token_count: bool = DEFAULT_CONSOLE_SHOW_TOKEN_COUNT

    # resolved/internal properties (set after initialization)
    resolved_input_paths: List[Path] = field(default_factory=list, init=False)
    base_dir: Path = field(init=False)  # must be an absolute path

    def __post_init__(self):
        """finalizes and validates config, primarily setting `base_dir`."""
        # determine base_dir: from resolved input_paths if any, else cwd.
        # `resolved_input_paths` (absolute, existing) are set by `discovery.py` or cli path resolution.
        # this __post_init__ is called after all fields are populated by cli/config logic.

        # if self.input_paths were not resolved to absolute yet by cli, do it now for base_dir.
        # however, cli.py now handles initial path resolution for base_dir determination.
        resolved_cli_paths: Set[Path] = set()
        if (
            self.input_paths
        ):  # self.input_paths are paths specified by user (cli or config)
            for p_user in self.input_paths:
                try:
                    # if path from config is relative, it's relative to config file location,
                    # or more simply, assume all paths from config/cli are resolved against cwd if not absolute.
                    p = Path(p_user)
                    abs_p = p if p.is_absolute() else Path.cwd() / p
                    if abs_p.exists():  # only consider existing paths for base_dir
                        resolved_cli_paths.add(abs_p.resolve())  # resolve symlinks too
                    else:
                        log.warning(
                            "input_path_not_found_for_basedir", path=str(p_user)
                        )
                except Exception as e:
                    log.warning(
                        "error_resolving_input_path_for_basedir",
                        path=str(p_user),
                        error=str(e),
                    )

        if resolved_cli_paths:
            # if multiple input paths, base_dir is determined from the first one.
            # a more complex strategy could find common ancestor.
            first_path = sorted(list(resolved_cli_paths))[0]
            self.base_dir = (
                first_path if first_path.is_dir() else first_path.parent
            ).resolve()
        else:  # no valid input paths from cli/config, or only stdin
            self.base_dir = Path.cwd().resolve()
            log.info(
                "base_dir_defaulted_to_cwd",
                reason="no valid input paths or only stdin",
                path=str(self.base_dir),
            )

        if not self.base_dir.is_absolute():  # this should not happen after .resolve()
            raise ConfigError(
                f"internal error: base_dir '{self.base_dir}' is not absolute."
            )

        if self.template_path and self.preset_template:
            log.warning(
                "custom_template_overrides_preset",
                custom_template=str(self.template_path),
                preset=self.preset_template.value,
            )

        log.debug(
            "promptconfig_finalized",
            base_dir=str(self.base_dir),
            num_input_paths=len(self.input_paths),
        )