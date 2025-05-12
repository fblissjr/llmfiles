# llmfiles/discovery.py
"""
file discovery and filtering logic.
handles walking directories, respecting .gitignore, and applying include/exclude patterns.
"""
import os
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Set, Dict

import pathspec  # type: ignore # for .gitignore and glob pattern matching
import structlog  # for structured logging

from llmfiles.config import PromptConfig
from llmfiles.exceptions import DiscoveryError

log = structlog.get_logger(__name__)  # module-level logger


def _read_gitignore_spec(directory_path: Path) -> Optional[pathspec.PathSpec]:
    """reads and parses a .gitignore file in the given directory, returning a pathspec object."""
    gitignore_file = directory_path / ".gitignore"
    if gitignore_file.is_file():
        try:
            with gitignore_file.open("r", encoding="utf-8", errors="ignore") as f_obj:
                # gitwildmatchpattern handles standard .gitignore syntax.
                spec = pathspec.PathSpec.from_lines(
                    pathspec.patterns.GitWildMatchPattern, f_obj
                )
                log.debug("loaded_gitignore", path=str(gitignore_file))
                return spec
        except Exception as e:
            log.warning(
                "failed_to_parse_gitignore", path=str(gitignore_file), error=str(e)
            )
    return None

def _compile_glob_patterns_to_spec(
    glob_patterns: List[str],
) -> Optional[pathspec.PathSpec]:
    """compiles a list of glob patterns into a pathspec object for efficient matching."""
    if not glob_patterns:
        return None
    try:
        # uses git-style wildcard matching for include/exclude patterns.
        return pathspec.PathSpec.from_lines(
            pathspec.patterns.GitWildMatchPattern, glob_patterns
        )
    except Exception as e:  # pathspec can raise errors for invalid patterns.
        raise DiscoveryError(
            f"error compiling glob patterns: {glob_patterns}. details: {e}"
        ) from e

def _determine_initial_seed_paths(config: PromptConfig) -> Set[Path]:
    """
    resolves and validates initial paths from cli arguments or stdin.
    these paths are the starting points for discovery.
    updates `config.resolved_input_paths` with absolute, existing paths.
    """
    raw_paths_from_user: List[Path] = []
    if config.read_from_stdin:
        log.info("reading_initial_paths_from_stdin")
        try:
            # read bytes to handle nul-separation correctly.
            stdin_content_bytes = (
                sys.stdin.buffer.read()
                if config.nul_separated
                else sys.stdin.read().encode("utf-8")
            )
            path_separator = b"\0" if config.nul_separated else b"\n"
            for path_str_bytes in stdin_content_bytes.split(path_separator):
                path_str = path_str_bytes.decode("utf-8", errors="replace").strip()
                if path_str:
                    raw_paths_from_user.append(
                        Path(path_str)
                    )  # keep as potentially relative for now
            log.debug("read_paths_from_stdin", count=len(raw_paths_from_user))
        except Exception as e:
            raise DiscoveryError(f"fatal error reading paths from stdin: {e}") from e
    else:
        raw_paths_from_user.extend(config.input_paths)  # from cli arguments

    resolved_seed_paths: Set[Path] = set()
    for raw_path in raw_paths_from_user:
        try:
            # resolve paths relative to cwd if not absolute, then get absolute path.
            # `strict=true` ensures the path exists.
            abs_path = raw_path if raw_path.is_absolute() else Path.cwd() / raw_path
            resolved_p = abs_path.resolve(strict=True)
            resolved_seed_paths.add(resolved_p)
        except FileNotFoundError:
            log.warning("initial_path_not_found", path=str(raw_path))
        except Exception as e:
            log.warning(
                "error_resolving_initial_path", path=str(raw_path), error=str(e)
            )

    config.resolved_input_paths = sorted(
        list(resolved_seed_paths)
    )  # store for reference
    if not resolved_seed_paths and raw_paths_from_user:
        log.warning(
            "no_valid_initial_paths_resolved", provided_count=len(raw_paths_from_user)
        )
    return resolved_seed_paths

def discover_paths(config: PromptConfig) -> Iterator[Path]:
    """
    discovers and yields absolute file paths matching all criteria in `config`.
    this is the main generator for finding files to include in the prompt.
    """
    log.info("path_discovery_started", base_dir_for_filters=str(config.base_dir))

    # determine starting points for discovery.
    # `_determine_initial_seed_paths` also populates `config.resolved_input_paths`.
    seed_paths_for_discovery = _determine_initial_seed_paths(config)
    if not seed_paths_for_discovery:
        log.info("no_valid_seed_paths_found_for_discovery")
        return

    # compile include/exclude patterns for efficient matching.
    include_spec = _compile_glob_patterns_to_spec(config.include_patterns)
    exclude_spec = _compile_glob_patterns_to_spec(config.exclude_patterns)

    # cache for .gitignore specs to avoid re-reading files.
    gitignore_specs_cache: Dict[Path, Optional[pathspec.PathSpec]] = {}
    # keep track of yielded files and walked directories to avoid duplicates/cycles.
    yielded_absolute_files: Set[Path] = set()
    visited_absolute_dirs_for_walk: Set[Path] = set()

    # process each seed path; it can be a file or a directory.
    # using a queue simulates a breadth-first approach for initial seeds if multiple are given.
    processing_queue: List[Path] = sorted(list(seed_paths_for_discovery))

    while processing_queue:
        current_path_to_process = processing_queue.pop(0)  # get next path from queue

        # if the seed path is a file, check it directly.
        if current_path_to_process.is_file():
            if current_path_to_process not in yielded_absolute_files:
                # path for filtering rules must be relative to `config.base_dir`.
                path_relative_to_base = (
                    current_path_to_process.relative_to(config.base_dir)
                    if current_path_to_process.is_relative_to(config.base_dir)
                    else current_path_to_process.name
                )  # fallback for files outside base_dir (use filename)

                if _should_yield_path(
                    current_path_to_process,
                    path_relative_to_base,
                    config,
                    include_spec,
                    exclude_spec,
                    gitignore_specs_cache,
                    is_dir=False,
                ):
                    yield current_path_to_process
                    yielded_absolute_files.add(current_path_to_process)
            continue  # done with this file

        # if the seed path is a directory, initiate `os.walk`.
        if current_path_to_process.is_dir():
            # avoid re-walking if this exact directory path was already processed (e.g. due to symlinks or overlapping seeds).
            if (
                current_path_to_process in visited_absolute_dirs_for_walk
                and not config.follow_symlinks
            ):
                log.debug(
                    "skipping_already_walked_dir", path=str(current_path_to_process)
                )
                continue
            visited_absolute_dirs_for_walk.add(current_path_to_process)

            log.debug("walking_directory", path=str(current_path_to_process))

            # `os.walk` recursively explores the directory tree.
            for root_dir_str, subdir_names, file_names_in_root in os.walk(
                current_path_to_process,
                topdown=True,  # `topdown=true` allows pruning of `subdir_names` list.
                followlinks=config.follow_symlinks,
                onerror=lambda err_obj: log.warning(
                    "os_walk_error",
                    path=getattr(err_obj, "filename", "unknown"),
                    error=getattr(err_obj, "strerror", str(err_obj)),
                ),
            ):
                current_walk_root_absolute = Path(root_dir_str).resolve()

                # --- filter subdirectories to prune traversal ---
                # iterate over a copy of `subdir_names` because we modify the original list in place.
                surviving_subdir_names = []
                for subdir_name in subdir_names:
                    subdir_absolute_path = (
                        current_walk_root_absolute / subdir_name
                    ).resolve()
                    # path for filtering rules is relative to `config.base_dir`.
                    path_relative_to_base = (
                        subdir_absolute_path.relative_to(config.base_dir)
                        if subdir_absolute_path.is_relative_to(config.base_dir)
                        else subdir_absolute_path
                    )  # fallback if dir is outside base_dir hierarchy

                    if _should_yield_path(
                        subdir_absolute_path,
                        path_relative_to_base,
                        config,
                        include_spec,
                        exclude_spec,
                        gitignore_specs_cache,
                        is_dir=True,
                    ):
                        surviving_subdir_names.append(
                            subdir_name
                        )  # keep this subdir for `os.walk` to descend into.
                    else:
                        log.debug(
                            "pruning_dir_from_walk",
                            path=str(subdir_absolute_path),
                            relative_to_base=str(path_relative_to_base),
                        )
                subdir_names[:] = (
                    surviving_subdir_names  # modify `os.walk`'s list in-place.
                )

                # --- process files in the current `current_walk_root_absolute` directory ---
                for file_name in file_names_in_root:
                    file_absolute_path = (
                        current_walk_root_absolute / file_name
                    ).resolve()
                    if (
                        file_absolute_path in yielded_absolute_files
                    ):  # avoid yielding duplicates.
                        continue

                    path_relative_to_base = (
                        file_absolute_path.relative_to(config.base_dir)
                        if file_absolute_path.is_relative_to(config.base_dir)
                        else file_absolute_path.name
                    )  # fallback for files outside base_dir.

                    if _should_yield_path(
                        file_absolute_path,
                        path_relative_to_base,
                        config,
                        include_spec,
                        exclude_spec,
                        gitignore_specs_cache,
                        is_dir=False,
                    ):
                        yield file_absolute_path
                        yielded_absolute_files.add(file_absolute_path)

        elif (
            not current_path_to_process.exists()
        ):  # should have been caught by `resolve(strict=true)` earlier.
            log.warning(
                "path_disappeared_during_discovery", path=str(current_path_to_process)
            )


def _should_yield_path(
    absolute_path: Path,  # the absolute path of the item (file or directory).
    path_relative_to_base: Path,  # path of the item relative to `config.base_dir`, used for glob/gitignore.
    config: PromptConfig,
    include_spec: Optional[pathspec.PathSpec],
    exclude_spec: Optional[pathspec.PathSpec],
    gitignore_cache: Dict[Path, Optional[pathspec.PathSpec]],
    is_dir: bool,  # true if `absolute_path` is a directory.
) -> bool:
    """
    determines if a file should be yielded or a directory traversed, based on all filters.
    order of checks: hidden -> gitignore -> exclude patterns -> include patterns.
    """
    # 1. hidden check: skip hidden items if not configured to include them.
    #    a path part is hidden if it starts with '.' (but isn't just '.' or '..').
    if (
        any(
            p.startswith(".") and p not in (".", "..")
            for p in path_relative_to_base.parts
        )
        and not config.hidden
    ):
        log.debug("filter_skip_hidden", path=str(path_relative_to_base))
        return False

    # 2. gitignore check: skip if ignored by any relevant .gitignore file.
    if not config.no_ignore:
        # check .gitignore files from the item's containing directory up to `config.base_dir`.
        # (or filesystem root if `config.base_dir` is not an ancestor, which is an edge case).
        dir_to_scan_for_gitignore = (
            absolute_path.parent if not is_dir else absolute_path
        )

        # iterate upwards while `dir_to_scan_for_gitignore` is valid and within reasonable bounds.
        while dir_to_scan_for_gitignore.is_dir() and (
            dir_to_scan_for_gitignore == config.base_dir
            or config.base_dir.is_relative_to(dir_to_scan_for_gitignore.parent)
            or not absolute_path.is_relative_to(config.base_dir)
        ):  # complex condition to define search scope
            if (
                dir_to_scan_for_gitignore not in gitignore_cache
            ):  # cache .gitignore specs.
                gitignore_cache[dir_to_scan_for_gitignore] = _read_gitignore_spec(
                    dir_to_scan_for_gitignore
                )

            current_gitignore_spec = gitignore_cache[dir_to_scan_for_gitignore]
            if current_gitignore_spec:
                try:
                    # path for matching must be relative to the directory of the .gitignore file.
                    path_for_spec_match = str(
                        absolute_path.relative_to(dir_to_scan_for_gitignore)
                    )
                    if current_gitignore_spec.match_file(path_for_spec_match):
                        log.debug(
                            "filter_skip_gitignored",
                            path=str(path_relative_to_base),
                            matched_as=path_for_spec_match,
                            gitignore_location=str(
                                dir_to_scan_for_gitignore / ".gitignore"
                            ),
                        )
                        return False
                except (
                    ValueError
                ):  # `absolute_path` is not relative to `dir_to_scan_for_gitignore`.
                    log.debug(
                        "path_not_relative_to_gitignore_dir_for_check",
                        path=str(absolute_path),
                        gitignore_dir=str(dir_to_scan_for_gitignore),
                    )

            # stop upward search if we've checked .gitignore in `config.base_dir` (and path is under it).
            if (
                dir_to_scan_for_gitignore == config.base_dir
                and absolute_path.is_relative_to(config.base_dir)
            ):
                break
            if (
                dir_to_scan_for_gitignore.parent == dir_to_scan_for_gitignore
            ):  # reached filesystem root.
                break
            dir_to_scan_for_gitignore = dir_to_scan_for_gitignore.parent

    # 3. glob exclude/include pattern checks (using `path_relative_to_base`).
    path_str_for_glob_match = str(path_relative_to_base)

    # check exclude patterns first.
    if exclude_spec and exclude_spec.match_file(path_str_for_glob_match):
        # if it matches an exclude pattern, it's out unless `include_priority` saves it.
        if (
            config.include_priority
            and include_spec
            and include_spec.match_file(path_str_for_glob_match)
        ):
            log.debug("filter_glob_priority_include", path=str(path_relative_to_base))
            # it's not excluded due to priority; proceed to final include check (which it just passed).
        else:
            log.debug("filter_glob_exclude", path=str(path_relative_to_base))
            return False  # definitely excluded.

    # check include patterns.
    if include_spec:
        # for FILES: if --include patterns are given, the file *must* match one.
        if not is_dir:
            if not include_spec.match_file(path_str_for_glob_match):
                log.debug(
                    "filter_glob_no_include_match_file", path=str(path_relative_to_base)
                )
                return False
            log.debug("filter_glob_include_match_file", path=str(path_relative_to_base))
            return True  # file matched include, wasn't excluded (or saved by priority).
        else:  # for DIRECTORIES:
            # if include patterns exist, we don't strictly require the directory name itself
            # to match file-centric patterns (e.g., `**/*.py`). the directory is allowed
            # for traversal so files *within* it can be checked against include patterns.
            # an include pattern *could* explicitly match a directory (e.g., `src/`, `**/tests/`).
            # `pathspec.match_file` on `path_str_for_glob_match` (relative dir path) handles this.
            # if `include_spec.match_file` is false for a dir, it might be because includes are file-only.
            # in this case, we still traverse. if an include *was* dir-specific and didn't match,
            # then `include_spec.match_file` would be false, but our permissive stance here for dirs means
            # we rely on `exclude_spec` or gitignore to prune unwanted directory trees.
            # this ensures `llmfiles . --include **/*.py` explores all non-excluded subdirs.
            log.debug(
                "filter_glob_include_dir_traversal_allowed",
                path=str(path_relative_to_base),
            )
            return True  # allow directory for traversal.
    else:  # no include_spec (no --include patterns were provided).
        # if it passed hidden, gitignore, and exclude checks, it's included.
        log.debug(
            "filter_glob_no_include_spec_item_included", path=str(path_relative_to_base)
        )
        return True