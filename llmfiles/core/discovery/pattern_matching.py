# llmfiles/core/discovery/pattern_matching.py
"""
Handles .gitignore style pattern matching, include/exclude glob compilation,
and the core filtering logic for determining if a path should be processed.
"""
from pathlib import Path
from typing import Optional, List, Dict
import pathspec # type: ignore
import structlog

from llmfiles.config.settings import PromptConfig # For type hinting
from llmfiles.exceptions import DiscoveryError # For error reporting

log = structlog.get_logger(__name__)

def load_gitignore_patterns_from_file(gitignore_file_path: Path) -> Optional[pathspec.PathSpec]:
    """Loads and compiles .gitignore patterns from a specific .gitignore file."""
    if not gitignore_file_path.is_file():
        return None
    try:
        with gitignore_file_path.open("r", encoding="utf-8", errors="ignore") as f_obj:
            spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, f_obj)
            log.debug("gitignore_patterns_loaded_and_compiled", path=str(gitignore_file_path))
            return spec
    except Exception as e:
        log.warning("failed_to_parse_gitignore_file", path=str(gitignore_file_path), error=str(e))
    return None

def compile_glob_patterns_to_spec(glob_patterns: List[str]) -> Optional[pathspec.PathSpec]:
    """Compiles a list of string glob patterns into a pathspec.PathSpec object."""
    if not glob_patterns:
        return None
    try:
        # Using GitWildMatchPattern for .gitignore-like glob syntax (supports **, !, etc.)
        spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, glob_patterns)
        log.debug("glob_patterns_compiled_to_spec", count=len(glob_patterns))
        return spec
    except Exception as e: # pathspec can raise errors for invalid patterns
        log.error("glob_pattern_compilation_failed", patterns=glob_patterns, error=str(e))
        raise DiscoveryError(f"Error compiling glob patterns {glob_patterns}: {e}") from e

def is_path_hidden(path_relative_to_root: Path, config: PromptConfig) -> bool:
    """Checks if a path is considered hidden based on leading dots and config."""
    if config.hidden: # If --hidden is used, nothing is considered hidden by this rule
        return False
    # A path is hidden if any of its parts start with a dot (excluding '.' or '..')
    return any(part.startswith(".") and part not in (".", "..") for part in path_relative_to_root.parts)

def is_path_gitignored(
    absolute_path_item: Path, 
    config: PromptConfig, 
    gitignore_specs_cache: Dict[Path, Optional[pathspec.PathSpec]]
) -> bool:
    """
    Checks if an item is ignored by any relevant .gitignore files.
    Paths are checked from the item's directory up to config.base_dir.
    """
    if config.no_ignore:
        return False

    # Traverse from item's parent directory upwards to config.base_dir
    current_dir_to_check = absolute_path_item.parent

    while True:
        if current_dir_to_check not in gitignore_specs_cache:
            gitignore_specs_cache[current_dir_to_check] = load_gitignore_patterns_from_file(
                current_dir_to_check / ".gitignore"
            )
        
        spec = gitignore_specs_cache[current_dir_to_check]
        if spec:
            # pathspec matches paths relative to the directory containing the .gitignore
            path_relative_to_gitignore = absolute_path_item.relative_to(current_dir_to_check)
            if spec.match_file(str(path_relative_to_gitignore)):
                log.debug("item_matched_gitignore_rule", item_path=str(absolute_path_item), 
                          gitignore_file=str(current_dir_to_check / ".gitignore"))
                return True # Ignored

        # Stop conditions for traversal
        if current_dir_to_check == config.base_dir: break
        if current_dir_to_check.parent == current_dir_to_check: break # Filesystem root
        if not config.base_dir.is_relative_to(current_dir_to_check.parent): # optimization for general case
            if not str(config.base_dir).startswith(str(current_dir_to_check.parent)):
                 break


        current_dir_to_check = current_dir_to_check.parent
    return False

def check_glob_match_rules(
    path_for_glob_matching: Path, # Typically relative to config.base_dir
    config: PromptConfig,
    include_spec: Optional[pathspec.PathSpec],
    exclude_spec: Optional[pathspec.PathSpec],
    is_item_a_directory: bool
) -> bool:
    """
    Applies include/exclude glob patterns to determine if an item should be kept.
    Returns True if item should be kept, False if it should be filtered out.
    """
    path_str_for_globs = str(path_for_glob_matching)

    is_explicitly_excluded = False
    if exclude_spec and exclude_spec.match_file(path_str_for_globs):
        is_explicitly_excluded = True
    
    is_explicitly_included = False
    if include_spec and include_spec.match_file(path_str_for_globs):
        is_explicitly_included = True

    if is_explicitly_excluded:
        if config.include_priority and is_explicitly_included:
            log.debug("glob_filter_kept_by_include_priority", path=path_str_for_globs)
            return True # Include overrides exclude
        log.debug("glob_filter_excluded_by_pattern", path=path_str_for_globs)
        return False # Excluded

    if include_spec: # If --include patterns are active
        if not is_explicitly_included:
            # If it's a file, it MUST match an include pattern.
            # If it's a directory, it can pass even if it doesn't directly match an include,
            # because files *within* it might match. Directory pruning for os.walk happens there.
            if not is_item_a_directory:
                log.debug("glob_filter_file_did_not_match_any_include", path=path_str_for_globs)
                return False
            # For directories, not matching an include directly is fine, allow traversal.
        log.debug("glob_filter_item_included_by_pattern_or_is_traversable_dir", path=path_str_for_globs)
        return True
    
    # No include_spec defined, and not excluded by any rule -> item is kept.
    log.debug("glob_filter_kept_no_includes_defined_and_not_excluded", path=path_str_for_globs)
    return True