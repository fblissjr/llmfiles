# llmfiles/discovery.py
"""File discovery and filtering logic."""
import os
import sys
import logging
from pathlib import Path
from typing import Iterator, List, Optional, Set, Dict

import pathspec  # type: ignore # For .gitignore and glob pattern matching

from .config import PromptConfig
from .exceptions import DiscoveryError

logger = logging.getLogger(__name__)

def _read_gitignore(dir_path: Path) -> Optional[pathspec.PathSpec]:
    """Reads and parses a .gitignore file from the specified directory."""
    gitignore_file = dir_path / ".gitignore"
    if gitignore_file.is_file():
        try:
            with gitignore_file.open("r", encoding="utf-8", errors="ignore") as f:
                # GitWildMatchPattern handles standard .gitignore syntax.
                return pathspec.PathSpec.from_lines(
                    pathspec.patterns.GitWildMatchPattern, f
                )
        except Exception as e:
            logger.warning(f"Could not read/parse {gitignore_file}: {e}")
    return None

def _build_pathspec(patterns: List[str]) -> Optional[pathspec.PathSpec]:
    """Builds a PathSpec object from glob patterns."""
    if not patterns:
        return None
    try:
        return pathspec.PathSpec.from_lines(
            pathspec.patterns.GitWildMatchPattern, patterns
        )
    except Exception as e:
        raise DiscoveryError(f"Error building pathspec from patterns '{patterns}': {e}")

def _get_seed_paths(config: PromptConfig) -> Set[Path]:
    """Resolves initial absolute paths from CLI args or stdin, populating config.resolved_input_paths."""
    raw_paths: List[Path] = []
    if config.read_from_stdin:
        logger.info("Reading initial paths from stdin...")
        try:
            content = (
                sys.stdin.buffer.read()
                if config.nul_separated
                else sys.stdin.read().encode("utf-8")
            )
            sep = b"\0" if config.nul_separated else b"\n"
            for p_str_bytes in content.split(sep):
                p_str = p_str_bytes.decode("utf-8", errors="replace").strip()
                if p_str:
                    raw_paths.append(Path(p_str))
        except Exception as e:
            raise DiscoveryError(f"Fatal error reading stdin: {e}")
    else:
        raw_paths.extend(config.input_paths)

    resolved_seeds: Set[Path] = set()
    for p_raw in raw_paths:
        try:
            resolved_seeds.add(
                (p_raw if p_raw.is_absolute() else Path.cwd() / p_raw).resolve(
                    strict=True
                )
            )
        except FileNotFoundError:
            logger.warning(f"Initial path '{p_raw}' not found. Skipping.")
        except Exception as e:
            logger.warning(f"Error resolving initial path '{p_raw}': {e}. Skipping.")

    config.resolved_input_paths = sorted(list(resolved_seeds))  # Store for reference
    return resolved_seeds

def discover_paths(config: PromptConfig) -> Iterator[Path]:
    """
    Discovers and yields absolute file paths matching configuration criteria.
    Handles .gitignore, include/exclude patterns, hidden files, and symlinks.
    """
    logger.info(
        f"Starting path discovery. Base directory for filters: {config.base_dir}"
    )
    seed_paths = _get_seed_paths(config)
    if not seed_paths:
        logger.info("No valid seed paths for discovery.")
        return

    yielded_files: Set[Path] = set()
    # visited_dirs for os.walk helps if follow_symlinks is true and cycles exist,
    # or if multiple seed_paths point to overlapping directory structures.
    visited_dirs_for_walk: Set[Path] = set()

    include_spec = _build_pathspec(config.include_patterns)
    exclude_spec = _build_pathspec(config.exclude_patterns)
    gitignore_cache: Dict[Path, Optional[pathspec.PathSpec]] = {}

    # Process initial seed paths (can be files or directories)
    path_queue: List[Path] = sorted(list(seed_paths))  # Process consistently
    while path_queue:
        current_seed_path = path_queue.pop(0)

        if current_seed_path.is_file():
            if current_seed_path not in yielded_files:
                path_rel_to_base = (
                    current_seed_path.relative_to(config.base_dir)
                    if current_seed_path.is_relative_to(config.base_dir)
                    else current_seed_path.name
                )  # Fallback for files outside base_dir
                if _should_yield_item(
                    current_seed_path,
                    path_rel_to_base,
                    config,
                    include_spec,
                    exclude_spec,
                    gitignore_cache,
                    False,
                ):
                    yield current_seed_path
                    yielded_files.add(current_seed_path)
            continue

        # If it's a directory, initiate os.walk from here
        if current_seed_path.is_dir():
            if (
                current_seed_path in visited_dirs_for_walk
                and not config.follow_symlinks
            ):
                continue  # Already walked this specific directory path
            visited_dirs_for_walk.add(current_seed_path)
            logger.debug(f"Walking directory: {current_seed_path}")

            for root_str, dirnames, filenames in os.walk(
                current_seed_path,
                topdown=True,
                followlinks=config.follow_symlinks,
                onerror=lambda e: logger.warning(f"os.walk error: {e}"),
            ):
                root_abs = Path(root_str).resolve()

                # Prune directories from traversal
                original_dirnames = list(dirnames)
                dirnames[:] = []  # Clear for repopulation
                for d_name in original_dirnames:
                    dir_abs = (root_abs / d_name).resolve()
                    path_rel_to_base = (
                        dir_abs.relative_to(config.base_dir)
                        if dir_abs.is_relative_to(config.base_dir)
                        else dir_abs
                    )  # Fallback if outside
                    if _should_yield_item(
                        dir_abs,
                        path_rel_to_base,
                        config,
                        include_spec,
                        exclude_spec,
                        gitignore_cache,
                        True,
                    ):
                        dirnames.append(d_name)
                    else:
                        logger.debug(
                            f"Pruning dir from walk: {dir_abs} (rel: {path_rel_to_base})"
                        )

                # Process files in current directory
                for f_name in filenames:
                    file_abs = (root_abs / f_name).resolve()
                    if file_abs in yielded_files:
                        continue
                    path_rel_to_base = (
                        file_abs.relative_to(config.base_dir)
                        if file_abs.is_relative_to(config.base_dir)
                        else file_abs.name
                    )
                    if _should_yield_item(
                        file_abs,
                        path_rel_to_base,
                        config,
                        include_spec,
                        exclude_spec,
                        gitignore_cache,
                        False,
                    ):
                        yield file_abs
                        yielded_files.add(file_abs)

def _should_yield_item(
    abs_path: Path,
    path_rel_to_base: Path,
    config: PromptConfig,
    include_spec: Optional[pathspec.PathSpec],
    exclude_spec: Optional[pathspec.PathSpec],
    gitignore_cache: Dict[Path, Optional[pathspec.PathSpec]],
    is_dir: bool,
) -> bool:
    """Determines if a file/directory should be yielded or traversed based on all filters."""

    # 1. Hidden Check (uses path relative to base for consistent dot-part checking)
    if (
        any(p.startswith(".") and p not in (".", "..") for p in path_rel_to_base.parts)
        and not config.hidden
    ):
        logger.debug(f"Filter [Hidden]: Skipping '{path_rel_to_base}'")
        return False

    # 2. Gitignore Check
    if not config.no_ignore:
        current_dir = abs_path.parent if not is_dir else abs_path
        # Iterate upwards from item's directory to base_dir (or root if base_dir is not an ancestor)
        while current_dir.is_dir() and (
            current_dir == config.base_dir
            or config.base_dir.is_relative_to(current_dir.parent)
            or not abs_path.is_relative_to(config.base_dir)
        ):
            if current_dir not in gitignore_cache:
                gitignore_cache[current_dir] = _read_gitignore(current_dir)
            spec = gitignore_cache[current_dir]
            if spec:
                try:
                    # Path for .gitignore matching is relative to the .gitignore's directory
                    path_for_gitignore_match = abs_path.relative_to(current_dir)
                    if spec.match_file(
                        str(path_for_gitignore_match)
                    ):  # Pathspec expects string
                        logger.debug(
                            f"Filter [Gitignore]: '{path_rel_to_base}' ignored by {current_dir / '.gitignore'}"
                        )
                        return False
                except ValueError:
                    pass  # Path not relative to current gitignore dir (e.g. different drive on windows)
            if current_dir == config.base_dir and abs_path.is_relative_to(
                config.base_dir
            ):
                break  # Checked base_dir's .gitignore
            if current_dir.parent == current_dir:
                break  # Filesystem root
            current_dir = current_dir.parent

    # 3. Exclude/Include Glob Patterns (uses path_rel_to_base)
    path_str_for_glob = str(path_rel_to_base)

    # Check Excludes: If excluded, it's out unless include_priority saves it.
    if exclude_spec and exclude_spec.match_file(path_str_for_glob):
        if (
            config.include_priority
            and include_spec
            and include_spec.match_file(path_str_for_glob)
        ):
            logger.debug(
                f"Filter [Glob Prio]: '{path_str_for_glob}' incld by priority despite exclude."
            )
            # Falls through to standard include logic for files, dirs are traversed
        else:
            logger.debug(f"Filter [Glob Exclude]: '{path_str_for_glob}' excluded.")
            return False

    # Check Includes:
    if include_spec:
        # For FILES: must match an include pattern if --include is used.
        if not is_dir and not include_spec.match_file(path_str_for_glob):
            logger.debug(
                f"Filter [Glob Include]: File '{path_str_for_glob}' doesn't match include. Skipping."
            )
            return False
        # For DIRECTORIES: if include_spec exists, we allow traversal.
        # Files *within* will be tested. This ensures `**/*.py` enters subdirs.
        # If an include pattern specifically targets this directory (e.g. `src/`), pathspec would match it.
        # If include patterns are only for files (e.g. `*.py`), pathspec won't match the dir name,
        # but we still need to traverse it. So, for dirs, this check effectively passes if not excluded earlier.
        if is_dir:
            logger.debug(
                f"Filter [Glob Include]: Dir '{path_str_for_glob}' allowed for traversal to check contents."
            )

    # If no include_spec, or if it's a file that matched, or a dir allowed for traversal.
    logger.debug(f"Filter [Passed]: '{path_str_for_glob}' (is_dir={is_dir})")
    return True