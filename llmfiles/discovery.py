# llmfiles/discovery.py
"""
file discovery and filtering logic.
handles walking directories, respecting .gitignore, and applying include/exclude patterns.
"""
import os
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Set, Dict, Union

import pathspec  # type: ignore
import structlog

from llmfiles.config import PromptConfig
from llmfiles.exceptions import DiscoveryError

log = structlog.get_logger(__name__)  # module-level logger


def _read_gitignore_spec(directory_path: Path) -> Optional[pathspec.PathSpec]:
    gitignore_file = directory_path / ".gitignore"
    if gitignore_file.is_file():
        try:
            with gitignore_file.open("r", encoding="utf-8", errors="ignore") as f_obj:
                spec = pathspec.PathSpec.from_lines(
                    pathspec.patterns.GitWildMatchPattern, f_obj
                )
                log.debug("loaded_gitignore_spec", path=str(gitignore_file))
                return spec
        except Exception as e:
            log.warning(
                "failed_to_parse_gitignore", path=str(gitignore_file), error=str(e)
            )
    return None

def _compile_glob_patterns_to_spec(
    glob_patterns: List[str],
) -> Optional[pathspec.PathSpec]:
    if not glob_patterns:
        return None
    try:
        return pathspec.PathSpec.from_lines(
            pathspec.patterns.GitWildMatchPattern, glob_patterns
        )
    except Exception as e:
        raise DiscoveryError(
            f"error compiling glob patterns: {glob_patterns}. details: {e}"
        ) from e

def _determine_initial_seed_paths(config: PromptConfig) -> Set[Path]:
    raw_paths_from_user: List[Path] = []
    # input_paths in config are already path objects from cli conversion or toml parsing (if paths were strings)
    user_provided_paths = config.input_paths

    if config.read_from_stdin:
        log.info("reading_initial_paths_from_stdin")
        try:
            stdin_content_bytes = (
                sys.stdin.buffer.read()
                if config.nul_separated
                else sys.stdin.read().encode("utf-8")
            )
            path_separator = b"\0" if config.nul_separated else b"\n"
            for path_str_bytes in stdin_content_bytes.split(path_separator):
                path_str = path_str_bytes.decode("utf-8", errors="replace").strip()
                if path_str:
                    raw_paths_from_user.append(Path(path_str))
            log.debug("read_paths_from_stdin", count=len(raw_paths_from_user))
        except Exception as e:
            raise DiscoveryError(f"fatal error reading paths from stdin: {e}") from e
    else:  # if not stdin, use paths from config (which might be from cli or file)
        if not user_provided_paths:  # if --input-path was not used and not stdin
            log.info("no_input_paths_provided_defaulting_to_cwd_for_seeds")
            raw_paths_from_user.append(
                Path(".")
            )  # default to current directory as the seed
        else:
            raw_paths_from_user.extend(user_provided_paths)

    resolved_seed_paths: Set[Path] = set()
    for raw_path in raw_paths_from_user:
        try:
            # paths from user can be relative to cwd or absolute.
            abs_path = raw_path if raw_path.is_absolute() else Path.cwd() / raw_path
            resolved_p = abs_path.resolve(
                strict=True
            )  # ensure path exists and is absolute
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
            "no_valid_initial_paths_resolved_from_user_input",
            provided_count=len(raw_paths_from_user),
        )
    return resolved_seed_paths


def discover_paths(config: PromptConfig) -> Iterator[Path]:
    # (main discover_paths walk logic remains the same as previous refactor)
    # ...
    log.info("path_discovery_started", base_dir_for_filters=str(config.base_dir))
    seed_paths_for_discovery = _determine_initial_seed_paths(config)
    if not seed_paths_for_discovery:
        log.info("no_valid_seed_paths_found_for_discovery")
        return

    include_spec = _compile_glob_patterns_to_spec(config.include_patterns)
    exclude_spec = _compile_glob_patterns_to_spec(config.exclude_patterns)

    gitignore_specs_cache: Dict[Path, Optional[pathspec.PathSpec]] = {}
    yielded_absolute_files: Set[Path] = set()
    visited_absolute_dirs_for_walk: Set[Path] = set()

    processing_queue: List[Path] = sorted(list(seed_paths_for_discovery))
    while processing_queue:
        current_path_to_process = processing_queue.pop(0)

        if current_path_to_process.is_file():
            if current_path_to_process not in yielded_absolute_files:
                # determine path relative to base_dir for filtering rules.
                # if item is outside base_dir, its own name is used for glob matching against patterns
                # that don't have directory components (e.g. "*.py" vs "src/*.py")
                path_rel_to_base = (
                    current_path_to_process.relative_to(config.base_dir)
                    if current_path_to_process.is_relative_to(config.base_dir)
                    else Path(current_path_to_process.name)
                )  # fallback if not under base_dir.

                if _should_yield_path(
                    current_path_to_process,
                    path_rel_to_base,
                    config,
                    include_spec,
                    exclude_spec,
                    gitignore_specs_cache,
                    is_dir=False,
                ):
                    yield current_path_to_process
                    yielded_absolute_files.add(current_path_to_process)
            continue

        if current_path_to_process.is_dir():
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
            for root_dir_str, subdir_names, file_names_in_root in os.walk(
                current_path_to_process,
                topdown=True,
                followlinks=config.follow_symlinks,
                onerror=lambda err: log.warning(
                    "os_walk_error",
                    path=str(getattr(err, "filename", err)),
                    error=str(getattr(err, "strerror", err)),
                ),
            ):
                current_walk_root_absolute = Path(root_dir_str).resolve()
                surviving_subdir_names = []  # subdirs to continue walking
                for subdir_name in subdir_names:
                    subdir_absolute_path = (
                        current_walk_root_absolute / subdir_name
                    ).resolve()
                    path_relative_to_base = (
                        subdir_absolute_path.relative_to(config.base_dir)
                        if subdir_absolute_path.is_relative_to(config.base_dir)
                        else subdir_absolute_path
                    )  # use absolute path itself if it's outside base_dir scope for filtering

                    if _should_yield_path(
                        subdir_absolute_path,
                        path_relative_to_base,
                        config,
                        include_spec,
                        exclude_spec,
                        gitignore_specs_cache,
                        is_dir=True,
                    ):
                        surviving_subdir_names.append(subdir_name)
                    else:
                        log.debug(
                            "pruning_dir_from_walk", path=str(subdir_absolute_path)
                        )
                subdir_names[:] = surviving_subdir_names  # modify os.walk's list

                for file_name in file_names_in_root:
                    file_absolute_path = (
                        current_walk_root_absolute / file_name
                    ).resolve()
                    if file_absolute_path in yielded_absolute_files:
                        continue
                    path_relative_to_base = (
                        file_absolute_path.relative_to(config.base_dir)
                        if file_absolute_path.is_relative_to(config.base_dir)
                        else Path(file_absolute_path.name)
                    )
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
        elif not current_path_to_process.exists():
            log.warning(
                "path_disappeared_during_discovery", path=str(current_path_to_process)
            )


def _should_yield_path(
    absolute_path: Path,
    path_relative_to_config_base: Path,  # path of item relative to `config.base_dir` (primary for globs)
    config: PromptConfig,
    include_spec: Optional[pathspec.PathSpec],
    exclude_spec: Optional[pathspec.PathSpec],
    gitignore_cache: Dict[Path, Optional[pathspec.PathSpec]],
    is_dir: bool,
) -> bool:
    # 1. hidden check (uses path_relative_to_config_base)
    if (
        any(
            p.startswith(".") and p not in (".", "..")
            for p in path_relative_to_config_base.parts
        )
        and not config.hidden
    ):
        log.debug(
            "filter_skip_hidden",
            item_type="dir" if is_dir else "file",
            path_for_filter=str(path_relative_to_config_base),
            abs_path=str(absolute_path),
        )
        return False

    # 2. gitignore check (this is the most complex part for scoping)
    if not config.no_ignore:
        # collect all directories to check for .gitignore files:
        # from the item's directory up to filesystem root, and from config.base_dir up to filesystem root.
        # use a set to avoid redundant checks.
        dirs_to_check_for_gitignores: Set[Path] = set()

        # hierarchy of the item itself
        p_item: Path = absolute_path.parent if not is_dir else absolute_path
        while p_item.is_dir() and p_item.parent != p_item:  # go up to root
            dirs_to_check_for_gitignores.add(p_item)
            p_item = p_item.parent
        dirs_to_check_for_gitignores.add(p_item)  # add root itself

        # hierarchy of config.base_dir (which is cwd)
        p_base: Path = config.base_dir
        while p_base.is_dir() and p_base.parent != p_base:
            dirs_to_check_for_gitignores.add(p_base)
            p_base = p_base.parent
        dirs_to_check_for_gitignores.add(p_base)

        # sort directories from deepest to shallowest (more specific .gitignore rules take precedence)
        # or sort shallowest to deepest, and if a match is found, it might be overridden by a deeper `!pattern`.
        # git's behavior: deeper .gitignore overrides parent, unless parent has `!/foo`.
        # pathspec usually handles this if matching a list of specs.
        # for simplicity here, we check each .gitignore. if any ignores, it's ignored.
        # this doesn't perfectly model `!pattern` overrides across multiple .gitignore files.
        # a more accurate model would collect all specs and check against all of them.

        for gitignore_dir_candidate in sorted(
            list(dirs_to_check_for_gitignores), key=lambda x: len(x.parts), reverse=True
        ):  # process deeper first
            if gitignore_dir_candidate not in gitignore_cache:
                gitignore_cache[gitignore_dir_candidate] = _read_gitignore_spec(
                    gitignore_dir_candidate
                )

            current_spec = gitignore_cache[gitignore_dir_candidate]
            if current_spec:
                try:
                    # path for matching must be relative to the .gitignore file's directory.
                    path_to_match_against_spec_str = str(
                        absolute_path.relative_to(gitignore_dir_candidate)
                    )
                    if current_spec.match_file(path_to_match_against_spec_str):
                        log.debug(
                            "filter_skip_gitignored",
                            item_type="dir" if is_dir else "file",
                            path_rel_to_base=str(
                                path_relative_to_config_base
                            ),  # for user context
                            matched_as=path_to_match_against_spec_str,  # what pathspec saw
                            gitignore_location=str(
                                gitignore_dir_candidate / ".gitignore"
                            ),
                        )
                        return False
                except (
                    ValueError
                ):  # absolute_path is not relative to gitignore_dir_candidate
                    pass  # this gitignore is not relevant for this path's hierarchy

    # 3. glob exclude/include pattern check (uses path_relative_to_config_base)
    # path_relative_to_config_base is used for matching these project-level globs.
    path_str_for_glob_match = str(path_relative_to_config_base)

    if exclude_spec and exclude_spec.match_file(path_str_for_glob_match):
        if (
            config.include_priority
            and include_spec
            and include_spec.match_file(path_str_for_glob_match)
        ):
            log.debug(
                "filter_glob_priority_include_override",
                item_type="dir" if is_dir else "file",
                path=path_str_for_glob_match,
            )
        else:
            log.debug(
                "filter_glob_exclude_match",
                item_type="dir" if is_dir else "file",
                path=path_str_for_glob_match,
            )
            return False

    if include_spec:
        if not is_dir:  # files must match an include pattern if specified.
            if not include_spec.match_file(path_str_for_glob_match):
                log.debug(
                    "filter_glob_no_include_match_for_file",
                    file_path=path_str_for_glob_match,
                )
                return False
            log.debug(
                "filter_glob_include_match_for_file", file_path=path_str_for_glob_match
            )
            return True
        else:  # directories are allowed for traversal if not excluded, to find matching files within.
            log.debug(
                "filter_glob_include_dir_allowed_for_traversal",
                dir_path=path_str_for_glob_match,
            )
            return True
    else:  # no --include patterns were given.
        log.debug(
            "filter_glob_no_include_spec_item_allowed",
            item_type="dir" if is_dir else "file",
            path=path_str_for_glob_match,
        )
        return True