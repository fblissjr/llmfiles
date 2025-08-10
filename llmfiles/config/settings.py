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

@dataclass
class PromptConfig:
    # holds all configuration parameters for a single run.
    input_paths: List[Path] = field(default_factory=list)
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    chunk_strategy: ChunkStrategy = ChunkStrategy.STRUCTURE
    no_ignore: bool = False
    hidden: bool = False
    follow_symlinks: bool = False
    line_numbers: bool = False
    no_codeblock: bool = False
    output_file: Optional[Path] = None
    read_from_stdin: bool = False
    nul_separated: bool = False

    # internal state, not set directly by user flags.
    base_dir: Path = field(init=False)

    def __post_init__(self):
        # performs initial setup after dataclass instantiation.
        self.base_dir = Path.cwd().resolve()
