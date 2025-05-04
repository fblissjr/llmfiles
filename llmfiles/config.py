# llmfiles/config.py
"""Configuration dataclasses and enums."""
import sys
import logging  # Make sure logging is imported if used in __post_init__
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Dict

# Ensure logging is configured if PromptConfig uses it
logger = logging.getLogger(__name__)


class SortMethod(Enum):
    NAME_ASC = "name_asc"
    NAME_DESC = "name_desc"
    DATE_ASC = "date_asc"
    DATE_DESC = "date_desc"

    @classmethod
    def from_string(cls, s: str) -> Optional['SortMethod']:
        try:
            return cls(s.lower())
        except ValueError:
            return None

class OutputFormat(Enum):
    MARKDOWN = "markdown"
    XML = "xml"
    JSON = "json"

    @classmethod
    def from_string(cls, s: str) -> Optional['OutputFormat']:
        try:
            return cls(s.lower())
        except ValueError:
            return None

class TokenCountFormat(Enum):
    HUMAN = "human"
    RAW = "raw"

    @classmethod
    def from_string(cls, s: str) -> Optional['TokenCountFormat']:
        try:
            return cls(s.lower())
        except ValueError:
            return None

# --- THIS ENUM MUST BE PRESENT ---
class PresetTemplate(Enum):
    DEFAULT = "default"
    CLAUDE_OPTIMAL = "claude-optimal"
    GENERIC_XML = "generic-xml"

    @classmethod
    def from_string(cls, s: str) -> Optional["PresetTemplate"]:
        try:
            return cls(s.lower())
        except ValueError:
            return None


# --- END OF REQUIRED ENUM ---

# Need exceptions for __post_init__
from .exceptions import ConfigError


@dataclass
class PromptConfig:
    """Configuration for llmfiles."""
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
    preset_template: Optional[PresetTemplate] = None  # Relies on the enum above
    user_vars: Dict[str, str] = field(default_factory=dict)
    output_format: OutputFormat = OutputFormat.MARKDOWN
    line_numbers: bool = False
    no_codeblock: bool = False
    absolute_paths: bool = False
    sort_method: SortMethod = SortMethod.NAME_ASC
    diff: bool = False
    git_diff_branch: Optional[Tuple[str, str]] = None
    git_log_branch: Optional[Tuple[str, str]] = None
    encoding: str = "cl100k"
    show_tokens_format: Optional[TokenCountFormat] = None
    output_file: Optional[Path] = None
    clipboard: bool = False

    resolved_input_paths: List[Path] = field(default_factory=list, init=False)
    base_dir: Optional[Path] = field(default=None, init=False)

    def __post_init__(self):
        """Validate paths after initialization."""
        # Determine base directory
        if self.input_paths:
            first_path = self.input_paths[0]
            try:
                resolved_first = first_path.resolve(strict=True)
                self.base_dir = (
                    resolved_first if resolved_first.is_dir() else resolved_first.parent
                )
            except FileNotFoundError:
                raise ConfigError(f"Input path does not exist: {first_path}")
            except Exception as e:
                raise ConfigError(f"Error resolving path {first_path}: {e}")
        elif self.read_from_stdin:
            self.base_dir = Path.cwd()  # Default to CWD if only stdin
            logger.info(f"Reading from stdin, using base directory: {self.base_dir}")
        else:
            raise ConfigError("No input paths provided and not reading from stdin.")

        # Resolve all input paths
        self.resolved_input_paths = []
        for p in self.input_paths:
            try:
                self.resolved_input_paths.append(p.resolve(strict=True))
            except FileNotFoundError:
                # Use print to stderr for user visibility, logger for internal tracking
                print(f"Warning: Input path skipped (not found): {p}", file=sys.stderr)
                logger.warning(f"Input path skipped (not found): {p}")
            except Exception as e:
                print(f"Warning: Error resolving path {p}: {e}", file=sys.stderr)
                logger.warning(f"Error resolving path {p}: {e}")

        # Validation for conflicting template options
        if self.template_path and self.preset_template:
            logger.warning(
                "Both --template and --preset provided. Custom --template will be used."
            )
            # PromptConfig is immutable after __post_init__ in standard dataclasses
            # Logic to prioritize should happen during _load_template in TemplateRenderer
            # No need to modify self.preset_template here.

        if not self.base_dir:
            raise ConfigError("Could not determine base directory.")