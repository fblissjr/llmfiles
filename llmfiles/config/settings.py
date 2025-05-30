# llmfiles/config/settings.py
"""
Defines the core PromptConfig dataclass, configuration-related Enums,
and default constant values for the llmfiles application.
"""
import structlog
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

log = structlog.get_logger(__name__)

# --- Configuration Enums --- (SortMethod, OutputFormat, TokenCountFormat, PresetTemplate, ChunkStrategy remain unchanged)

class SortMethod(Enum):
    NAME_ASC, NAME_DESC, DATE_ASC, DATE_DESC = "name_asc", "name_desc", "date_asc", "date_desc"
    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["SortMethod"]:
        if not s: return None
        try: return cls(s.lower())
        except ValueError: log.warning("invalid_sort_method_string", input_string=s); return None

class OutputFormat(Enum):
    MARKDOWN, XML, JSON = "markdown", "xml", "json"
    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["OutputFormat"]:
        if not s: return None
        try: return cls(s.lower())
        except ValueError: log.warning("invalid_output_format_string", input_string=s); return None

class TokenCountFormat(Enum):
    HUMAN, RAW = "human", "raw"
    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["TokenCountFormat"]:
        if not s: return None
        try: return cls(s.lower())
        except ValueError: log.warning("invalid_token_count_format_string", input_string=s); return None

class PresetTemplate(Enum):
    DEFAULT, CLAUDE_OPTIMAL, GENERIC_XML = "default", "claude-optimal", "generic-xml"
    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["PresetTemplate"]:
        if not s: return None
        try: return cls(s.lower())
        except ValueError: log.warning("invalid_preset_template_string", input_string=s); return None

class ChunkStrategy(Enum):
    FILE = "file" 
    PYTHON_STRUCTURE = "python_structure"
    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["ChunkStrategy"]:
        if not s: return None
        try: return cls(s.lower())
        except ValueError: log.warning("invalid_chunk_strategy_string", input_string=s); return None

# --- Default Constants ---
# DEFAULT_YAML_TRUNCATION_PLACEHOLDER = "<content truncated due to length>" # Removed
# DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN = 500 # Removed
DEFAULT_CONSOLE_SHOW_TREE = True
DEFAULT_CONSOLE_SHOW_SUMMARY = True
DEFAULT_CONSOLE_SHOW_TOKEN_COUNT = False
DEFAULT_OUTPUT_FORMAT = OutputFormat.MARKDOWN
DEFAULT_SORT_METHOD = SortMethod.NAME_ASC
DEFAULT_ENCODING = "cl100k_base"
DEFAULT_CHUNK_STRATEGY = ChunkStrategy.FILE

@dataclass
class PromptConfig:
    """Holds all configuration parameters for processing and prompt generation."""
    # Input sources
    input_paths: List[Path] = field(default_factory=lambda: [Path(".")])
    read_from_stdin: bool = False
    nul_separated: bool = False

    # Filtering
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    include_from_files: List[Path] = field(default_factory=list)
    exclude_from_files: List[Path] = field(default_factory=list)
    include_priority: bool = False
    no_ignore: bool = False
    hidden: bool = False
    follow_symlinks: bool = False

    # Chunking and Content Processing
    chunk_strategy: ChunkStrategy = DEFAULT_CHUNK_STRATEGY
    # process_yaml_truncate_long_fields: bool = False # Removed
    # yaml_truncate_placeholder: str = DEFAULT_YAML_TRUNCATION_PLACEHOLDER # Removed
    # yaml_truncate_content_max_len: int = DEFAULT_YAML_TRUNCATE_CONTENT_MAX_LEN # Removed
    
    # Output Templating & Formatting
    template_path: Optional[Path] = None
    preset_template: Optional[PresetTemplate] = None
    user_vars: Dict[str, str] = field(default_factory=dict)
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT
    line_numbers: bool = False
    no_codeblock: bool = False
    absolute_paths: bool = False 
    show_absolute_project_path: bool = False 

    # Git Integration
    diff: bool = False
    git_diff_branch: Optional[Tuple[str, str]] = None
    git_log_branch: Optional[Tuple[str, str]] = None

    # Token Counting
    encoding: str = DEFAULT_ENCODING
    show_tokens_format: Optional[TokenCountFormat] = None

    # Output Destinations & Behavior
    output_file: Optional[Path] = None
    clipboard: bool = False
    sort_method: SortMethod = DEFAULT_SORT_METHOD

    # Console Output Preferences
    console_show_tree: bool = DEFAULT_CONSOLE_SHOW_TREE
    console_show_summary: bool = DEFAULT_CONSOLE_SHOW_SUMMARY
    console_show_token_count: bool = DEFAULT_CONSOLE_SHOW_TOKEN_COUNT
    
    save_profile_name: Optional[str] = field(default=None, init=True)

    resolved_input_paths: List[Path] = field(default_factory=list, init=False)
    base_dir: Path = field(init=False) 

    def __post_init__(self):
        self.base_dir = Path.cwd().resolve()
        log.debug("PromptConfig.base_dir_initialized", path=str(self.base_dir))
        if self.template_path and self.preset_template:
            log.warning("custom_template_overrides_preset",
                        custom_template=str(self.template_path),
                        preset=self.preset_template.value if self.preset_template else "none")