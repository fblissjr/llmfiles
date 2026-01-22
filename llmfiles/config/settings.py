from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional
import structlog

log = structlog.get_logger(__name__)

class ChunkStrategy(Enum):
    # defines available strategies for breaking down file content.
    STRUCTURE = "structure"
    FILE = "file"

    @classmethod
    def from_string(cls, s: Optional[str]) -> Optional["ChunkStrategy"]:
        if not s:
            return None
        try:
            return cls(s.lower())
        except ValueError:
            log.warning("invalid_chunk_strategy_string", input_string=s)
            return None

class ExternalDepsStrategy(Enum):
    # defines how to handle external dependencies.
    IGNORE = "ignore"
    METADATA = "metadata"

    @classmethod
    def from_string(cls, s: Optional[str]) -> "ExternalDepsStrategy":
        if not s:
            return cls.IGNORE
        try:
            return cls(s.lower())
        except ValueError:
            log.warning("invalid_external_deps_strategy", input_string=s)
            return cls.IGNORE


class OutputFormat(Enum):
    # defines output format style.
    COMPACT = "compact"  # Optimized for LLM consumption (file index first, code, then deps)
    VERBOSE = "verbose"  # Legacy format with full dependency graph upfront

    @classmethod
    def from_string(cls, s: Optional[str]) -> "OutputFormat":
        if not s:
            return cls.COMPACT
        try:
            return cls(s.lower())
        except ValueError:
            log.warning("invalid_output_format", input_string=s)
            return cls.COMPACT

@dataclass
class PromptConfig:
    # holds all configuration parameters for a single run.
    input_paths: List[Path] = field(default_factory=list)
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    grep_content_pattern: Optional[str] = None
    chunk_strategy: ChunkStrategy = ChunkStrategy.FILE
    external_deps_strategy: ExternalDepsStrategy = ExternalDepsStrategy.IGNORE
    no_ignore: bool = False
    hidden: bool = False
    follow_symlinks: bool = False
    line_numbers: bool = False
    no_codeblock: bool = False
    exclude_binary: bool = True
    max_file_size: Optional[int] = None  # Maximum file size in bytes, None = no limit
    git_since: Optional[str] = None  # Git date filter (e.g., "7 days ago", "2025-01-01")
    output_file: Optional[Path] = None
    read_from_stdin: bool = False
    nul_separated: bool = False
    recursive: bool = False
    trace_calls: bool = False  # Use Jedi for semantic call tracing (Python only)
    output_format: OutputFormat = OutputFormat.COMPACT

    # internal state, can be set explicitly or defaults to cwd.
    base_dir: Optional[Path] = None

    def __post_init__(self):
        # performs initial setup after dataclass instantiation.
        if self.base_dir is None:
            self.base_dir = Path.cwd().resolve()
