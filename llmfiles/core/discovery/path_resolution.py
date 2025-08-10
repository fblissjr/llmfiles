import sys
from pathlib import Path
from typing import List, Set
import structlog

from llmfiles.config.settings import PromptConfig
from llmfiles.exceptions import DiscoveryError

log = structlog.get_logger(__name__)

def resolve_initial_seed_paths(config: PromptConfig) -> Set[Path]:
    # determines the starting set of absolute file/directory paths for discovery.
    user_provided_raw_paths: List[Path] = []

    if config.read_from_stdin:
        log.info("resolving_seed_paths_from_stdin")
        try:
            # read raw bytes from stdin, then decode.
            stdin_bytes = sys.stdin.buffer.read()
            path_separator = b"\0" if config.nul_separated else b"\n"

            for path_str_bytes in stdin_bytes.split(path_separator):
                path_str = path_str_bytes.decode("utf-8", errors="replace").strip()
                if path_str:
                    user_provided_raw_paths.append(Path(path_str))
        except Exception as e:
            raise DiscoveryError(f"fatal error reading seed paths from stdin: {e}")
    else:
        # use input_paths from config, defaulting to current directory.
        user_provided_raw_paths.extend(config.input_paths if config.input_paths else [Path(".")])

    resolved_absolute_paths: Set[Path] = set()
    for raw_path in user_provided_raw_paths:
        try:
            # resolve paths to be absolute and ensure they exist.
            abs_path = raw_path.resolve(strict=True)
            resolved_absolute_paths.add(abs_path)
        except FileNotFoundError:
            log.warning("seed_path_not_found_skipped", path_str=str(raw_path))
        except Exception as e:
            log.warning("error_resolving_seed_path", path_str=str(raw_path), error_message=str(e))

    if not resolved_absolute_paths and user_provided_raw_paths:
        log.warning("no_valid_seed_paths_resolved_from_user_input", provided_count=len(user_provided_raw_paths))

    log.info("initial_seed_paths_resolved", count=len(resolved_absolute_paths))
    return resolved_absolute_paths
