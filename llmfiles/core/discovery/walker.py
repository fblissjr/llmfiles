# llmfiles/core/discovery/walker.py
"""
Contains the main directory walking and path discovery generator function.
Orchestrates the use of path resolution and pattern matching utilities.
"""
import os
from pathlib import Path
from typing import Iterator, List, Dict, Optional, Set
import structlog

from llmfiles.config.settings import PromptConfig # For type hinting
from llmfiles.core.discovery.path_resolution import resolve_initial_seed_paths
from llmfiles.core.discovery.pattern_matching import (
    compile_glob_patterns_to_spec,
    is_path_hidden,
    is_path_gitignored,
    check_glob_match_rules,
    pathspec # For type hint PathSpec
)

log = structlog.get_logger(__name__)

def _should_process_path(
    absolute_path: Path, 
    path_for_filtering: Path, # Path relative to config.base_dir for filtering rules
    config: PromptConfig,
    include_spec: Optional[pathspec.PathSpec], 
    exclude_spec: Optional[pathspec.PathSpec],
    gitignore_cache: Dict[Path, Optional[pathspec.PathSpec]],
    is_dir: bool
) -> bool:
    """Consolidated filtering logic wrapper."""
    if is_path_hidden(path_for_filtering, config):
        log.debug("path_filtered_hidden", path=str(path_for_filtering))
        return False
    if is_path_gitignored(absolute_path, config, gitignore_cache):
        log.debug("path_filtered_gitignored", path=str(absolute_path))
        return False
    if not check_glob_match_rules(path_for_filtering, config, include_spec, exclude_spec, is_dir):
        # Logging done within check_glob_match_rules
        return False
    return True


def discover_paths(config: PromptConfig) -> Iterator[Path]:
    """
    Main discovery generator. Walks paths and yields absolute file Paths matching all criteria.
    Uses resolved seed paths and compiled pattern specs.
    """
    log.info("path_discovery_walker_started", base_dir=str(config.base_dir))
    
    # Prepare patterns and seed paths
    # Note: Modifying config directly here means patterns are globally changed for this run.
    # If _prepare_patterns was in pipeline.py, it modifies the config instance passed around.
    # Assuming patterns in config are already fully resolved (including from files) before this point.
    include_spec = compile_glob_patterns_to_spec(config.include_patterns)
    exclude_spec = compile_glob_patterns_to_spec(config.exclude_patterns)
    seed_paths_to_walk = resolve_initial_seed_paths(config)

    if not seed_paths_to_walk:
        log.info("no_seed_paths_to_walk_discovery_ending")
        return

    # Caches and tracking sets
    gitignore_specs_cache: Dict[Path, Optional[pathspec.PathSpec]] = {}
    yielded_absolute_files: Set[Path] = set()
    # Visited dirs for os.walk to prevent re-walking same physical dir via different symlinks if follow_symlinks is off
    # If follow_symlinks is on, os.walk handles cycles for file paths, but directory re-entry needs this.
    processed_dirs_by_os_walk: Set[Path] = set() 

    processing_queue: List[Path] = sorted(list(seed_paths_to_walk))

    while processing_queue:
        current_abs_path = processing_queue.pop(0) # Already resolved in seed_paths

        path_rel_to_base_for_filters = current_abs_path.relative_to(config.base_dir) \
            if current_abs_path.is_relative_to(config.base_dir) \
            else Path(current_abs_path.name) # Fallback for paths outside base_dir

        if current_abs_path.is_file():
            if current_abs_path not in yielded_absolute_files and \
               _should_process_path(current_abs_path, path_rel_to_base_for_filters, config, 
                                   include_spec, exclude_spec, gitignore_specs_cache, is_dir=False):
                log.debug("yielding_discovered_file", path=str(current_abs_path))
                yield current_abs_path
                yielded_absolute_files.add(current_abs_path)
            continue

        if current_abs_path.is_dir():
            # Check if we should even traverse into this directory
            if not _should_process_path(current_abs_path, path_rel_to_base_for_filters, config,
                                        include_spec, exclude_spec, gitignore_specs_cache, is_dir=True):
                log.debug("skipping_directory_traversal_filtered_out", dir_path=str(current_abs_path))
                continue

            if current_abs_path in processed_dirs_by_os_walk and not config.follow_symlinks:
                log.debug("skipping_directory_already_processed_by_os_walk", dir_path=str(current_abs_path))
                continue
            processed_dirs_by_os_walk.add(current_abs_path)

            log.debug("entering_directory_for_os_walk", dir_path=str(current_abs_path))
            for dir_root_str, sub_dir_names, current_file_names in os.walk(
                    str(current_abs_path), topdown=True, followlinks=config.follow_symlinks,
                    onerror=lambda err: log.warning("os_walk_access_error", details=str(err))):
                
                current_walk_root_abs = Path(dir_root_str).resolve()

                # Prune subdirectories from os.walk's list if they don't meet criteria
                original_sub_dir_names = list(sub_dir_names) # Iterate copy
                sub_dir_names[:] = [] # Modify in-place
                for sub_dir_name in original_sub_dir_names:
                    sub_dir_abs = (current_walk_root_abs / sub_dir_name).resolve()
                    sub_dir_rel_for_filters = sub_dir_abs.relative_to(config.base_dir) \
                        if sub_dir_abs.is_relative_to(config.base_dir) \
                        else Path(sub_dir_abs.name)
                    if _should_process_path(sub_dir_abs, sub_dir_rel_for_filters, config, 
                                           include_spec, exclude_spec, gitignore_specs_cache, is_dir=True):
                        sub_dir_names.append(sub_dir_name) # Keep for traversal
                    else:
                        log.debug("pruning_subdir_from_os_walk_list", path=str(sub_dir_abs))
                
                # Process files in current directory
                for file_name_in_dir in current_file_names:
                    file_abs = (current_walk_root_abs / file_name_in_dir).resolve()
                    if file_abs in yielded_absolute_files: continue

                    file_rel_for_filters = file_abs.relative_to(config.base_dir) \
                        if file_abs.is_relative_to(config.base_dir) \
                        else Path(file_abs.name)
                    
                    if _should_process_path(file_abs, file_rel_for_filters, config, 
                                           include_spec, exclude_spec, gitignore_specs_cache, is_dir=False):
                        log.debug("yielding_discovered_file_from_walk", path=str(file_abs))
                        yield file_abs
                        yielded_absolute_files.add(file_abs)
        
        elif not current_abs_path.exists(): # Edge case: path disappeared during processing
            log.warning("path_from_queue_no_longer_exists", path_str=str(current_abs_path))
            
    log.info("path_discovery_walker_finished")