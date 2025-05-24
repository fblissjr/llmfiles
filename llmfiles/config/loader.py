# llmfiles/config/loader.py
"""
Handles loading, merging, and saving of configurations from/to TOML files.
"""
import toml
from pathlib import Path
from typing import Dict, Any, List 
from dataclasses import asdict, fields as dataclass_fields, MISSING
from enum import Enum
import structlog

from llmfiles.exceptions import ConfigError

from .settings import PromptConfig, ChunkStrategy 

log = structlog.get_logger(__name__)

PROJECT_CONFIG_FILENAMES = [".llmfiles.toml", "llmfiles.toml", "pyproject.toml"]
USER_CONFIG_DIR = Path.home() / ".config" / "llmfiles"
USER_CONFIG_FILE = USER_CONFIG_DIR / "config.toml"

CONFIG_KEY_TO_PROMPTCONFIG_ATTR_MAP: Dict[str, str] = {
    "input_paths": "input_paths",
    "include_patterns": "include_patterns", 
    "exclude_patterns": "exclude_patterns", 
    "include_from_files": "include_from_files",
    "exclude_from_files": "exclude_from_files",
    "no_ignore": "no_ignore",
    "hidden": "hidden",
    "follow_symlinks": "follow_symlinks",
    "chunk_strategy": "chunk_strategy", 
    "template_path": "template_path", 
    "preset_template": "preset_template", 
    "user_vars": "user_vars", 
    "output_format": "output_format",
    "line_numbers": "line_numbers",
    "no_codeblock": "no_codeblock",
    "absolute_paths": "absolute_paths",
    "show_absolute_project_path": "show_absolute_project_path",
    # YAML truncation keys removed:
    # "process_yaml_truncate_long_fields": "process_yaml_truncate_long_fields",
    # "yaml_truncate_placeholder": "yaml_truncate_placeholder",
    # "yaml_truncate_content_max_len": "yaml_truncate_content_max_len",
    "sort_method": "sort_method", 
    "git_diff": "diff", 
    "git_diff_branch": "git_diff_branch",
    "git_log_branch": "git_log_branch",
    "encoding": "encoding",
    "show_tokens_format": "show_tokens_format",
    "output_file": "output_file",
    "clipboard": "clipboard",
    "console_show_tree": "console_show_tree",
    "console_show_summary": "console_show_summary",
    "console_show_token_count": "console_show_token_count",
}

# _load_toml_file_data and load_and_merge_configs functions remain the same as previously defined.
# For brevity, I'm omitting them here, but they should be present in your actual file.
# Ensure they are copied from the version in the previous multi-file response.

def _load_toml_file_data(file_path: Path) -> Dict[str, Any]:
    if not file_path.is_file(): return {}
    log.debug("loading_toml_config_file", path=str(file_path))
    try:
        data = toml.load(file_path)
        return data.get("tool", {}).get("llmfiles", {}) if file_path.name == "pyproject.toml" else data
    except Exception as e: log.error("config_file_load_error", path=str(file_path), error=str(e)); return {}

def load_and_merge_configs() -> Dict[str, Any]:
    merged_toml_data: Dict[str, Any] = {}
    if USER_CONFIG_FILE.is_file():
        log.info("loading_user_global_config", path=str(USER_CONFIG_FILE))
        merged_toml_data.update(_load_toml_file_data(USER_CONFIG_FILE))
    
    project_config_source_file: Optional[Path] = None
    for filename in PROJECT_CONFIG_FILENAMES:
        candidate = Path.cwd() / filename
        if candidate.is_file():
            log.info("loading_project_local_config", path=str(candidate))
            project_settings = _load_toml_file_data(candidate)
            if project_settings:
                user_profiles = merged_toml_data.get("profiles", {})
                project_profiles = project_settings.pop("profiles", {})
                if isinstance(user_profiles, dict) and isinstance(project_profiles, dict):
                    user_profiles.update(project_profiles)
                    merged_toml_data["profiles"] = user_profiles
                elif isinstance(project_profiles, dict):
                    merged_toml_data["profiles"] = project_profiles
                merged_toml_data.update(project_settings)
                project_config_source_file = candidate
                log.debug("project_config_applied", source_file=str(project_config_source_file))
                break 
    if not merged_toml_data: log.debug("no_configuration_files_loaded")
    # (Further logging about source can be added if desired)
    return merged_toml_data

# save_config_to_profile function remains the same.
# It will simply not save the YAML fields if they are not in PromptConfig / CONFIG_KEY_TO_PROMPTCONFIG_ATTR_MAP.
def save_config_to_profile(config_to_save: PromptConfig, profile_name: str):
    target_toml_path = Path.cwd() / ".llmfiles.toml"
    if not target_toml_path.exists():
        alt_path = Path.cwd() / "llmfiles.toml"
        if alt_path.exists(): target_toml_path = alt_path
    log.info("attempting_to_save_profile", profile=profile_name, path=str(target_toml_path))

    attrs_to_skip = {"base_dir", "resolved_input_paths", "save_profile_name", "read_from_stdin", "nul_separated"}
    profile_data: Dict[str, Any] = {}
    config_dict = asdict(config_to_save)

    for pc_attr, value in config_dict.items():
        if pc_attr in attrs_to_skip: continue
        toml_key = next((k for k, v in CONFIG_KEY_TO_PROMPTCONFIG_ATTR_MAP.items() if v == pc_attr), None)
        if not toml_key: continue

        field_def = next((f for f in dataclass_fields(PromptConfig) if f.name == pc_attr), None)
        if field_def:
            default_val = field_def.default_factory() if field_def.default_factory is not MISSING else field_def.default
            always_save = ("include_patterns", "exclude_patterns", "include_from_files", "exclude_from_files", "input_paths", "user_vars")
            if value == default_val and pc_attr not in always_save:
                continue
        
        if isinstance(value, Path): profile_data[toml_key] = str(value)
        elif isinstance(value, list) and all(isinstance(i, Path) for i in value): profile_data[toml_key] = [str(i) for i in value]
        elif isinstance(value, Enum): profile_data[toml_key] = value.value
        elif value is not None or (isinstance(value, (list, dict)) and pc_attr in always_save):
            profile_data[toml_key] = value
    
    if not profile_data:
        log.info("no_options_to_save_for_profile", profile=profile_name)
        # User message handled by CLI part
        return False

    try:
        existing_data: Dict[str, Any] = {}
        if target_toml_path.exists():
            try: existing_data = toml.load(target_toml_path)
            except toml.TomlDecodeError as e:
                raise ConfigError(f"Could not read existing TOML {target_toml_path} to save profile: {e}")
        
        if profile_name.upper() == "DEFAULT":
            profiles_bak = existing_data.pop("profiles", None)
            existing_data.update(profile_data)
            if profiles_bak is not None: existing_data["profiles"] = profiles_bak
        else:
            existing_data.setdefault("profiles", {})[profile_name] = profile_data
        
        with target_toml_path.open("w", encoding="utf-8") as f: toml.dump(existing_data, f)
        log.info("profile_saved_successfully", profile=profile_name, path=str(target_toml_path))
        return True
    except Exception as e:
        raise ConfigError(f"Error writing profile '{profile_name}' to {target_toml_path}: {e}")