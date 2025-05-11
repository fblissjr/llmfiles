# llmfiles/config.py
"""Configuration dataclasses and enums for llmfiles."""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Set

from .exceptions import ConfigError

logger = logging.getLogger(__name__)


class SortMethod(Enum):
    NAME_ASC, NAME_DESC, DATE_ASC, DATE_DESC = (
        "name_asc",
        "name_desc",
        "date_asc",
        "date_desc",
    )

    @classmethod
    def from_string(cls, s: str) -> Optional["SortMethod"]:
        return cls(s.lower()) if s else None


class OutputFormat(Enum):
    MARKDOWN, XML, JSON = "markdown", "xml", "json"

    @classmethod
    def from_string(cls, s: str) -> Optional["OutputFormat"]:
        return cls(s.lower()) if s else None


class TokenCountFormat(Enum):
    HUMAN, RAW = "human", "raw"

    @classmethod
    def from_string(cls, s: str) -> Optional["TokenCountFormat"]:
        return cls(s.lower()) if s else None


class PresetTemplate(Enum):
    DEFAULT, CLAUDE_OPTIMAL, GENERIC_XML = "default", "claude-optimal", "generic-xml"
    @classmethod
    def from_string(cls, s: str) -> Optional["PresetTemplate"]:
        return cls(s.lower()) if s else None

DEFAULT_YAML_TRUNCATION_PLACEHOLDER = "<content truncated due to length>"
DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN = 500

@dataclass
class PromptConfig:
    """Holds all configuration for generating a prompt."""
    input_paths: List[Path] = field(default_factory=list)
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
    output_format: OutputFormat = OutputFormat.MARKDOWN
    line_numbers: bool = False
    no_codeblock: bool = False
    absolute_paths: bool = False
    process_yaml_truncate_long_fields: bool = False
    yaml_truncate_placeholder: str = DEFAULT_YAML_TRUNCATION_PLACEHOLDER
    yaml_truncate_content_max_len: int = DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN
    sort_method: SortMethod = SortMethod.NAME_ASC
    diff: bool = False
    git_diff_branch: Optional[Tuple[str, str]] = None
    git_log_branch: Optional[Tuple[str, str]] = None
    encoding: str = "cl100k"
    show_tokens_format: Optional[TokenCountFormat] = None
    output_file: Optional[Path] = None
    clipboard: bool = False

    resolved_input_paths: List[Path] = field(default_factory=list, init=False)
    base_dir: Path = field(init=False)

    def __post_init__(self):
        """Determines `base_dir` after other fields are initialized."""
        candidate_paths: Set[Path] = set()
        if self.input_paths:  # Prefer explicitly provided paths for base_dir
            for p_str in self.input_paths:
                try:
                    candidate_paths.add(Path(p_str).resolve(strict=True))
                except FileNotFoundError:
                    logger.warning(f"Input path '{p_str}' for base_dir not found.")
                except Exception as e:
                    logger.warning(f"Error resolving '{p_str}' for base_dir: {e}")

        if candidate_paths:
            first_valid = next(iter(candidate_paths))
            self.base_dir = (
                first_valid if first_valid.is_dir() else first_valid.parent
            ).resolve()
        else:  # Fallback to CWD if no valid input_paths or only stdin
            self.base_dir = Path.cwd().resolve()
            logger.info(
                f"Base directory defaulted to CWD: {self.base_dir} (no valid input paths or only stdin)."
            )

        if not self.base_dir.is_absolute():  # Should be absolute after resolve()
            raise ConfigError(
                f"Internal error: base_dir '{self.base_dir}' is not absolute."
            )

        if self.template_path and self.preset_template:
            logger.warning(
                f"Custom template '{self.template_path}' will override preset '{self.preset_template.value}'."
            )
        logger.debug(
            f"PromptConfig initialized. Base_dir: {self.base_dir}. YAML truncate: {self.process_yaml_truncate_long_fields}"
        )