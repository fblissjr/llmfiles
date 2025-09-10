# llmfiles/core/discovery/walker.py
import os
from pathlib import Path
from typing import Iterator, Dict, Optional, Set, List
import structlog

from llmfiles.config.settings import PromptConfig
from llmfiles.core.discovery.path_resolution import resolve_initial_seed_paths
from llmfiles.core.discovery.pattern_matching import (
    compile_glob_patterns_to_spec,
    is_path_hidden,
    is_path_gitignored,
    pathspec
)

log = structlog.get_logger(__name__)

def discover_paths(config: PromptConfig) -> Iterator[Path]:
    #
    # >>> THE FIX IS HERE <<<
    # this is a new, simpler, and correct implementation of the file walker.
    #
    log.info("path_discovery_walker_started", base_dir=str(config.base_dir))

    # if the user provides a simple directory name as an include pattern,
    # convert it to a glob pattern that matches its contents.
    include_patterns: List[str] = []
    for p in config.include_patterns:
        if Path(p).is_dir():
            include_patterns.append(f"{p.rstrip('/')}/**/*")
        else:
            include_patterns.append(p)

    if not include_patterns:
        include_patterns.append('**/*')

    include_spec = compile_glob_patterns_to_spec(include_patterns)
    exclude_spec = compile_glob_patterns_to_spec(config.exclude_patterns)
    assert include_spec is not None

    seed_paths = resolve_initial_seed_paths(config)
    if not seed_paths:
        return

    gitignore_cache: Dict[Path, Optional[pathspec.PathSpec]] = {}
    yielded_files: Set[Path] = set()

    for seed_path in seed_paths:
        # process individual files given as arguments.
        if seed_path.is_file():
            rel_path = seed_path.relative_to(config.base_dir)
            path_str = rel_path.as_posix()
            if include_spec.match_file(path_str) and not (exclude_spec and exclude_spec.match_file(path_str)):
                if not is_path_hidden(rel_path, config) and not is_path_gitignored(seed_path, config, gitignore_cache):
                    if seed_path not in yielded_files:
                        yield seed_path
                        yielded_files.add(seed_path)
            continue

        # walk directories.
        for root, dirs, files in os.walk(str(seed_path), topdown=True, followlinks=config.follow_symlinks):
            # prune directories.
            dirs[:] = [
                d for d in dirs
                if not is_path_hidden(Path(root, d).relative_to(config.base_dir), config)
            ]

            for file_name in files:
                file_path = Path(root, file_name)
                rel_path = file_path.relative_to(config.base_dir)
                path_str = rel_path.as_posix()

                if is_path_hidden(rel_path, config):
                    continue
                if is_path_gitignored(file_path, config, gitignore_cache):
                    continue

                if include_spec.match_file(path_str):
                    if not (exclude_spec and exclude_spec.match_file(path_str)):
                        if file_path not in yielded_files:
                            yield file_path
                            yielded_files.add(file_path)


def grep_files_for_content(config: PromptConfig) -> Iterator[Path]:
    """
    Discovers files by searching for a pattern in their content.
    """
    if not config.grep_content_pattern:
        return

    log.info("grep_content_search_started", pattern=config.grep_content_pattern)

    # Use discover_paths to get a list of candidate files that respect all
    # other filtering rules (.gitignore, includes/excludes, etc.).
    candidate_files = discover_paths(config)

    for file_path in candidate_files:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if config.grep_content_pattern in content:
                log.debug("grep_pattern_found_in_file", file=str(file_path))
                yield file_path
        except Exception as e:
            log.warning("grep_file_read_error", file=str(file_path), error=str(e))
            continue
