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
    "include": "include_patterns",
    "exclude": "exclude_patterns",
    "include_from_files": "include_from_files",  # New mapping
    "exclude_from_files": "exclude_from_files",  # New mapping
    "no_ignore": "no_ignore",
    "hidden": "hidden",
    "follow_symlinks": "follow_symlinks",
    "template": "template_path",
    "preset": "preset_template",
    "vars": "user_vars",
    "output_format": "output_format",
    "line_numbers": "line_numbers",
    "no_codeblock": "no_codeblock",
    "absolute_paths": "absolute_paths",
    "yaml_truncate_long_fields": "process_yaml_truncate_long_fields",
    "yaml_placeholder": "yaml_truncate_placeholder",
    "yaml_max_len": "yaml_truncate_content_max_len",
    "sort": "sort_method",
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
    returns a dictionary of raw TOML keys and values.
    """
    merged_settings: Dict[str, Any] = {}

    # 1. load user-global configuration (lowest precedence)
    if USER_CONFIG_FILE.is_file():
        log.info("loading user config.", path=str(USER_CONFIG_FILE))
        user_loaded_settings = _load_settings_from_toml(USER_CONFIG_FILE)
        merged_settings.update(user_loaded_settings)

    # 2. load project-specific configuration (highest precedence among config files)
    project_config_loaded_from_file: Optional[Path] = None
    for filename in PROJECT_CONFIG_FILENAMES:
        project_file = Path.cwd() / filename
        if project_file.is_file():
            log.info("loading project config.", path=str(project_file))
            project_settings = _load_settings_from_toml(project_file)
            if project_settings:
                # Merge profiles carefully: project profiles override user profiles by name.
                if "profiles" in merged_settings and "profiles" in project_settings:
                    # Ensure both are dicts before attempting to update
                    if isinstance(merged_settings.get("profiles"), dict) and isinstance(
                        project_settings.get("profiles"), dict
                    ):
                        # Mypy/pyright might complain about potential None if .get() was used without check,
                        # but logic implies they are dicts here.
                        merged_settings["profiles"].update(project_settings["profiles"])  # type: ignore

                        # Remove 'profiles' from project_settings to avoid overwriting
                        # the merged 'profiles' dict during the general update below.
                        del project_settings["profiles"]
                    elif isinstance(project_settings.get("profiles"), dict):
                        # If user_settings didn't have profiles or it wasn't a dict,
                        # just use the project's profiles.
                        merged_settings["profiles"] = project_settings["profiles"]
                        if (
                            "profiles" in project_settings
                        ):  # Should exist if we are in this branch
                            del project_settings["profiles"]

                merged_settings.update(project_settings)
                project_config_loaded_from_file = project_file
                log.debug(
                    "project config applied.",
                    file=str(project_file),
                    settings_keys=list(project_settings.keys()),
                )
                break  # Load only the first project config file found

    if (
        not merged_settings and not project_config_loaded_from_file
    ):  # Corrected condition
        log.debug("no user or project configuration files found or loaded.")
    elif project_config_loaded_from_file:
        log.info(
            "final config source after merge includes project file.",
            project_file=str(project_config_loaded_from_file),
        )
    elif USER_CONFIG_FILE.is_file() and merged_settings:
        log.info(
            "final config source is user global file.", user_file=str(USER_CONFIG_FILE)
        )

    log.debug(
        "merged config defaults from files (raw TOML keys).", defaults=merged_settings
    )
    return merged_settings