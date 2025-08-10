# llmfiles/core/discovery/pattern_matching.py
from pathlib import Path
from typing import Optional, List, Dict
import pathspec
import structlog

from llmfiles.config.settings import PromptConfig
from llmfiles.exceptions import DiscoveryError

log = structlog.get_logger(__name__)

def load_gitignore_patterns_from_file(gitignore_file_path: Path) -> Optional[pathspec.PathSpec]:
    # loads and compiles .gitignore patterns from a given file.
    if not gitignore_file_path.is_file():
        return None
    try:
        with gitignore_file_path.open("r", encoding="utf-8", errors="ignore") as f_obj:
            spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, f_obj)
            return spec
    except Exception as e:
        log.warning("failed_to_parse_gitignore_file", path=str(gitignore_file_path), error=str(e))
    return None

def compile_glob_patterns_to_spec(glob_patterns: List[str]) -> Optional[pathspec.PathSpec]:
    # compiles a list of glob patterns into a pathspec object for matching.
    if not glob_patterns:
        return None
    try:
        return pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, glob_patterns)
    except Exception as e:
        raise DiscoveryError(f"error compiling glob patterns {glob_patterns}: {e}")

def is_path_hidden(path_relative_to_root: Path, config: PromptConfig) -> bool:
    # checks if a path is considered hidden based on leading dots.
    if config.hidden:
        return False
    return any(part.startswith(".") and part not in (".", "..") for part in path_relative_to_root.parts)

def is_path_gitignored(
    absolute_path_item: Path,
    config: PromptConfig,
    gitignore_specs_cache: Dict[Path, Optional[pathspec.PathSpec]],
) -> bool:
    # checks if an item is ignored by any relevant .gitignore files by traversing upwards.
    if config.no_ignore:
        return False

    current_dir_to_check = absolute_path_item.parent

    while True:
        if current_dir_to_check not in gitignore_specs_cache:
            gitignore_file = current_dir_to_check / ".gitignore"
            gitignore_specs_cache[current_dir_to_check] = load_gitignore_patterns_from_file(gitignore_file)

        spec = gitignore_specs_cache[current_dir_to_check]
        if spec:
            try:
                path_relative_to_gitignore_dir = absolute_path_item.relative_to(current_dir_to_check)
                path_str_for_match = path_relative_to_gitignore_dir.as_posix()

                if spec.match_file(path_str_for_match):
                    return True
            except ValueError:
                pass

        if current_dir_to_check.parent == current_dir_to_check:
            break
        current_dir_to_check = current_dir_to_check.parent

    return False

def check_glob_match_rules(
    path_for_glob_matching: Path,
    include_spec: pathspec.PathSpec, # now non-optional
    exclude_spec: Optional[pathspec.PathSpec],
) -> bool:
    #
    # >>> THE FIX IS HERE <<<
    # this implements the standard filtering logic:
    # 1. it must be on the include list.
    # 2. it must not be on the exclude list.
    #
    path_str = path_for_glob_matching.as_posix()

    # rule 1: the path must be explicitly included.
    if not include_spec.match_file(path_str):
        return False

    # rule 2: if it is included, it must not also be excluded.
    if exclude_spec and exclude_spec.match_file(path_str):
        return False

    # passed both checks: keep the file.
    return True
