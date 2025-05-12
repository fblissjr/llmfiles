# llmfiles/config_file.py
"""
handles loading and merging of default configurations from toml files.
allows users to set persistent defaults for llmfiles behavior.
"""
import toml # for parsing toml configuration files
from pathlib import Path
from typing import Dict, Any, List, Optional
import structlog # for structured logging

log = structlog.get_logger(__name__) # module-level logger

# defines standard names for project-local configuration files.
PROJECT_CONFIG_FILENAMES = [".llmfiles.toml", "llmfiles.toml", "pyproject.toml"]
# defines the user-level configuration file path.
USER_CONFIG_DIR = Path.home() / ".config" / "llmfiles"
USER_CONFIG_FILE = USER_CONFIG_DIR / "config.toml"

# map keys used in the config file (toml) to their corresponding PromptConfig attribute names.
# this allows flexibility in config file naming conventions.
# format: "config_file_key": "PromptConfig_attribute_name"
CONFIG_TO_PROMPTCONFIG_MAP: Dict[str, str] = {
    "input_paths": "input_paths",
    "include": "include_patterns", # 'include' in toml maps to 'include_patterns'
    "exclude": "exclude_patterns",
    "no_ignore": "no_ignore",
    "hidden": "hidden",
    "follow_symlinks": "follow_symlinks",
    "template": "template_path", # 'template' in toml maps to 'template_path'
    "preset": "preset_template", # 'preset' in toml maps to 'preset_template' (enum name)
    "vars": "user_vars",         # 'vars' in toml maps to 'user_vars'
    "output_format": "output_format", # enum name
    "line_numbers": "line_numbers",
    "no_codeblock": "no_codeblock",
    "absolute_paths": "absolute_paths",
    "yaml_truncate_long_fields": "process_yaml_truncate_long_fields",
    "yaml_placeholder": "yaml_truncate_placeholder",
    "yaml_max_len": "yaml_truncate_content_max_len",
    "sort": "sort_method", # enum name
    "git_diff": "diff",
    "git_diff_branch": "git_diff_branch",
    "git_log_branch": "git_log_branch",
    "encoding": "encoding",
    "show_tokens_format": "show_tokens_format", # enum name for json/stderr output
    "output_file": "output_file",
    "clipboard": "clipboard",
    # console specific output preferences
    "console_show_tree": "console_show_tree",
    "console_show_summary": "console_show_summary",
    "console_show_token_count": "console_show_token_count",
}

def _load_settings_from_toml(file_path: Path) -> Dict[str, Any]:
    """loads settings from a single toml file.
       expects settings under a `[tool.llmfiles]` table in pyproject.toml,
       or top-level for other .toml files.
    """
    if not file_path.is_file():
        return {}
    log.debug("loading config.", file_path=str(file_path))
    try:
        data = toml.load(file_path)
        # for pyproject.toml, settings are expected under [tool.llmfiles]
        if file_path.name == "pyproject.toml":
            return data.get("tool", {}).get("llmfiles", {})
        # for .llmfiles.toml or config.toml, settings can be top-level
        return data
    except toml.TomlDecodeError as e:
        log.error("failed to decode toml config.", file_path=str(file_path), error=str(e))
    except Exception as e:
        log.error("failed to load config file.", file_path=str(file_path), error=str(e), exc_info=True)
    return {}

def get_merged_config_defaults() -> Dict[str, Any]:
    """
    loads configurations from user and project files, merging them.
    project-specific files (cwd) override user-global files.
    returns a dictionary directly usable for `PromptConfig` kwargs after mapping keys.
    """
    merged_settings: Dict[str, Any] = {}

    # 1. load user-global configuration (lowest precedence)
    if USER_CONFIG_FILE.is_file():
        log.info("loading user config.", path=str(USER_CONFIG_FILE))
        merged_settings.update(_load_settings_from_toml(USER_CONFIG_FILE))

    # 2. load project-specific configuration (highest precedence among config files)
    #    searches for config files in the current working directory.
    #    pyproject.toml is often at project root, .llmfiles.toml can be too.
    project_config_loaded = False
    for filename in PROJECT_CONFIG_FILENAMES:
        project_file = Path.cwd() / filename
        if project_file.is_file():
            log.info("loading project config.", path=str(project_file))
            project_settings = _load_settings_from_toml(project_file)
            merged_settings.update(project_settings) # project settings override user settings
            if project_settings: # if a project config actually provided settings
                 project_config_loaded = True
                 log.debug("project config applied.", file=str(project_file), settings_keys=list(project_settings.keys()))
            # if you want to load only the first project config found: break
    
    if not merged_settings and not project_config_loaded:
        log.debug("no user or project configuration files found or loaded.")
    
    # map loaded setting keys to PromptConfig attribute names
    final_defaults_for_promptconfig: Dict[str, Any] = {}
    for conf_key, pc_attr_name in CONFIG_TO_PROMPTCONFIG_MAP.items():
        if conf_key in merged_settings:
            final_defaults_for_promptconfig[pc_attr_name] = merged_settings[conf_key]
            
    log.debug("merged config defaults for promptconfig.", defaults=final_defaults_for_promptconfig)
    return final_defaults_for_promptconfig