# llmfiles/config.py
"""Configuration dataclasses and enums."""
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Dict

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
    JSON = "json" # Represents JSON *output structure*, content often still uses other templates

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

@dataclass
class PromptConfig:
    """Configuration for smart-prompt-builder."""
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

    # Resolved paths after validation
    resolved_input_paths: List[Path] = field(default_factory=list, init=False)
    base_dir: Optional[Path] = field(default=None, init=False) # Primary dir for relative paths

    def __post_init__(self):
        """Validate paths after initialization."""
        if self.read_from_stdin:
            # In stdin mode, base_dir needs to be determined carefully,
            # perhaps using the first explicitly provided path or cwd.
            # For simplicity now, assume cwd if only stdin is used.
            self.base_dir = Path.cwd()
            if self.input_paths:
                # Use first explicit path as base if provided alongside stdin
                first_path = self.input_paths[0]
                try:
                     resolved = first_path.resolve(strict=True)
                     self.base_dir = resolved if resolved.is_dir() else resolved.parent
                     self.resolved_input_paths.append(resolved)
                except FileNotFoundError:
                    raise ConfigError(f"Input path does not exist: {first_path}")
                except Exception as e:
                     raise ConfigError(f"Error resolving path {first_path}: {e}")
            self.resolved_input_paths = [] # Paths will come from stdin stream
        else:
            if not self.input_paths:
                raise ConfigError("No input paths provided and not reading from stdin.")
            first_path = self.input_paths[0]
            try:
                resolved = first_path.resolve(strict=True)
                # Base dir is the first path if it's a dir, or its parent if it's a file
                self.base_dir = resolved if resolved.is_dir() else resolved.parent
                self.resolved_input_paths.append(resolved)
            except FileNotFoundError:
                raise ConfigError(f"Input path does not exist: {first_path}")
            except Exception as e:
                raise ConfigError(f"Error resolving path {first_path}: {e}")

            # Resolve remaining paths
            for p in self.input_paths[1:]:
                 try:
                     self.resolved_input_paths.append(p.resolve(strict=True))
                 except FileNotFoundError:
                    print(f"Warning: Input path skipped (not found): {p}", file=sys.stderr)
                 except Exception as e:
                     print(f"Warning: Error resolving path {p}: {e}", file=sys.stderr)

        if not self.base_dir:
             raise ConfigError("Could not determine base directory.")

# Need to import exceptions here to avoid circular dependency
from .exceptions import ConfigError