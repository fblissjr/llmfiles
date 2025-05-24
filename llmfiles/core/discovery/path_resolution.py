# llmfiles/core/discovery/path_resolution.py
"""
Handles the resolution of initial seed paths for the discovery process.
Determines the starting points for directory walking based on user input
(CLI arguments, stdin) and defaults.
"""
import sys
from pathlib import Path
from typing import List, Set
import structlog

from llmfiles.config.settings import PromptConfig # For type hinting
from llmfiles.exceptions import DiscoveryError # For error reporting

log = structlog.get_logger(__name__)

def resolve_initial_seed_paths(config: PromptConfig) -> Set[Path]:
    """
    Determines and resolves the initial set of absolute file or directory paths
    to begin the discovery process from. Updates config.resolved_input_paths.
    """
    user_provided_raw_paths: List[Path] = []

    if config.read_from_stdin:
        log.info("resolving_seed_paths_from_stdin")
        try:
            # Read raw bytes from stdin, then decode
            stdin_bytes = sys.stdin.buffer.read() if config.nul_separated else sys.stdin.read().encode("utf-8")
            path_separator = b"\0" if config.nul_separated else b"\n"
            
            for path_str_bytes in stdin_bytes.split(path_separator):
                path_str = path_str_bytes.decode("utf-8", errors="replace").strip()
                if path_str:
                    user_provided_raw_paths.append(Path(path_str))
            log.debug("raw_paths_read_from_stdin", count=len(user_provided_raw_paths))
        except Exception as e:
            raise DiscoveryError(f"Fatal error reading seed paths from stdin: {e}") from e
    else:
        # Use input_paths from config (which might be from CLI or config file)
        # Default to current directory if no paths are provided at all.
        user_provided_raw_paths.extend(config.input_paths if config.input_paths else [Path(".")])
        log.debug("using_seed_paths_from_config_input_paths", paths=user_provided_raw_paths)

    resolved_absolute_paths: Set[Path] = set()
    for raw_path in user_provided_raw_paths:
        try:
            # Paths can be relative to CWD or absolute. Resolve them.
            # .resolve(strict=True) ensures path exists and follows symlinks (once).
            # If a symlink target doesn't exist, it errors.
            # If strict=False, it resolves symlinks but doesn't error for non-existent paths (Path creates object).
            # For seed paths, they generally should exist.
            abs_path = raw_path.resolve(strict=True) 
            resolved_absolute_paths.add(abs_path)
        except FileNotFoundError:
            log.warning("seed_path_not_found_skipped", path_str=str(raw_path))
        except Exception as e: # Other potential errors like permission issues during resolve
            log.warning("error_resolving_seed_path", path_str=str(raw_path), error_message=str(e))

    # Store resolved paths in config for reference, if this pattern is used elsewhere.
    # However, PromptConfig.resolved_input_paths isn't used much by other parts currently.
    # config.resolved_input_paths = sorted(list(resolved_absolute_paths)) 
    
    if not resolved_absolute_paths and user_provided_raw_paths:
        log.warning("no_valid_seed_paths_resolved_from_user_input", provided_count=len(user_provided_raw_paths))
    
    log.info("initial_seed_paths_resolved", count=len(resolved_absolute_paths))
    return resolved_absolute_paths