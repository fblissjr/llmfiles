# llmfiles/discovery.py
"""File discovery and filtering logic."""
import os
import sys
import logging
from pathlib import Path
from typing import Iterator, List, Optional, Set, Tuple, Dict

import pathspec # For gitignore/glob matching

from .config import PromptConfig
from .exceptions import DiscoveryError

logger = logging.getLogger(__name__)

def _read_gitignore(path: Path) -> Optional[pathspec.PathSpec]:
    """Reads gitignore rules from a file."""
    gitignore_file = path / ".gitignore"
    if gitignore_file.is_file():
        try:
            with gitignore_file.open("r", encoding="utf-8") as f:
                spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, f)
                logger.debug(f"Loaded .gitignore from: {gitignore_file}")
                return spec
        except Exception as e:
            logger.warning(f"Could not read or parse {gitignore_file}: {e}")
    return None

def _build_glob_specs(config: PromptConfig) -> Tuple[Optional[pathspec.PathSpec], Optional[pathspec.PathSpec]]:
    """Builds pathspecs for include and exclude patterns."""
    include_spec = None
    exclude_spec = None
    try:
        if config.include_patterns:
            include_spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, config.include_patterns)
        if config.exclude_patterns:
            exclude_spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, config.exclude_patterns)
    except Exception as e:
        raise DiscoveryError(f"Error building glob patterns: {e}")
    return include_spec, exclude_spec

def discover_paths(config: PromptConfig) -> Iterator[Path]:
    """
    Discovers and yields file paths based on configuration, handling stdin and gitignore.
    Yields absolute paths.
    """
    logger.info("Starting path discovery...")
    processed_paths: Set[Path] = set()
    base_dir = config.base_dir.resolve() # Ensure base_dir is absolute

    # 1. Read paths from stdin if requested
    stdin_paths: List[Path] = []
    if config.read_from_stdin:
        logger.info("Reading paths from stdin...")
        try:
            if config.nul_separated:
                stdin_content = sys.stdin.buffer.read()
                paths_str = [p for p in stdin_content.split(b'\0') if p]
                stdin_paths = [(base_dir / Path(p.decode('utf-8'))).resolve() for p in paths_str]
            else:
                stdin_content = sys.stdin.read()
                paths_str = [p for p in stdin_content.splitlines() if p]
                stdin_paths = [(base_dir / Path(p)).resolve() for p in paths_str]
            logger.debug(f"Read {len(stdin_paths)} paths from stdin.")
        except Exception as e:
            raise DiscoveryError(f"Error reading from stdin: {e}")

    # 2. Combine stdin paths and resolved input paths
    all_start_paths = config.resolved_input_paths + stdin_paths
    if not all_start_paths:
         logger.warning("No valid input paths to process.")
         return

    include_spec, exclude_spec = _build_glob_specs(config)
    gitignore_specs = {} # Cache gitignore specs per directory

    # Use a queue for breadth-first search if needed, or simpler recursion for walk
    # os.walk is generally easier here for gitignore handling
    queue = list(all_start_paths)
    visited_dirs = set()

    while queue:
        current_path = queue.pop(0).resolve() # Ensure absolute

        # If it's a file, process directly
        if current_path.is_file():
             if current_path not in processed_paths:
                 # Apply filters relative to base_dir
                 relative_path_for_filter = current_path.relative_to(base_dir)
                 if _should_yield_path(current_path, relative_path_for_filter, base_dir, config, include_spec, exclude_spec, gitignore_specs):
                    yield current_path
                    processed_paths.add(current_path)
             continue

        # If it's a directory, walk it
        if current_path.is_dir() and current_path not in visited_dirs:
            visited_dirs.add(current_path)
            logger.debug(f"Walking directory: {current_path}")

            # Load gitignore for this directory level if not already loaded
            if not config.no_ignore and current_path not in gitignore_specs:
                gitignore_specs[current_path] = _read_gitignore(current_path)

            try:
                for root, dirs, files in os.walk(current_path, topdown=True, followlinks=config.follow_symlinks):
                    current_root = Path(root).resolve()
                    relative_root_for_filter = current_root.relative_to(base_dir)

                    # Filter directories first
                    effective_dirs = []
                    for d in dirs:
                        dir_path = (current_root / d).resolve()
                        relative_dir_path = dir_path.relative_to(base_dir)
                        if _should_yield_path(dir_path, relative_dir_path, base_dir, config, include_spec, exclude_spec, gitignore_specs, is_dir=True):
                            effective_dirs.append(d)
                    dirs[:] = effective_dirs # Modify dirs in place for os.walk

                    # Process files
                    for f in files:
                        file_path = (current_root / f).resolve()
                        if file_path in processed_paths:
                            continue
                        relative_file_path = file_path.relative_to(base_dir)
                        if _should_yield_path(file_path, relative_file_path, base_dir, config, include_spec, exclude_spec, gitignore_specs):
                            yield file_path
                            processed_paths.add(file_path)

            except OSError as e:
                 logger.warning(f"Could not walk directory {current_path}: {e}")


def _should_yield_path(
    absolute_path: Path,
    relative_path: Path, # Path relative to base_dir for filtering
    base_dir: Path,
    config: PromptConfig,
    include_spec: Optional[pathspec.PathSpec],
    exclude_spec: Optional[pathspec.PathSpec],
    gitignore_specs: Dict[Path, Optional[pathspec.PathSpec]],
    is_dir: bool = False
) -> bool:
    """Determines if a path should be included based on all filters."""

    # 1. Hidden file/dir check
    is_hidden = any(part.startswith('.') for part in relative_path.parts)
    if is_hidden and not config.hidden:
        logger.debug(f"Skipping hidden: {relative_path}")
        return False

    # 2. Gitignore check
    if not config.no_ignore:
         # Check gitignore files from current dir up to base_dir
         ignored_by_git = False
         current_parent = absolute_path.parent
         while current_parent >= base_dir:
              spec = gitignore_specs.get(current_parent) # Get potentially cached spec
              if spec is None and current_parent not in gitignore_specs: # Check if we tried and failed before
                  spec = _read_gitignore(current_parent)
                  gitignore_specs[current_parent] = spec # Cache result (even if None)

              if spec and spec.match_file(relative_path):
                    logger.debug(f"Skipping gitignored ({current_parent / '.gitignore'}): {relative_path}")
                    ignored_by_git = True
                    break
              if current_parent == base_dir: # Stop at base_dir
                  break
              current_parent = current_parent.parent
         if ignored_by_git:
             return False


    # 3. Glob include/exclude check
    # Use relative_path for matching against glob patterns
    path_str_for_glob = str(relative_path)

    excluded_by_glob = exclude_spec and exclude_spec.match_file(path_str_for_glob)
    included_by_glob = include_spec and include_spec.match_file(path_str_for_glob)

    if excluded_by_glob and not included_by_glob:
         logger.debug(f"Skipping excluded by glob: {relative_path}")
         return False
    if excluded_by_glob and included_by_glob and not config.include_priority:
         logger.debug(f"Skipping excluded by glob (priority): {relative_path}")
         return False
    if not included_by_glob and include_spec: # If include patterns exist, must match one
         logger.debug(f"Skipping not included by glob: {relative_path}")
         return False

    # If we passed all checks
    logger.debug(f"Including path: {relative_path}")
    return True