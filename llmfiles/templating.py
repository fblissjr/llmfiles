# llmfiles/templating.py
"""
Handles handlebars templating for generating the final prompt output.
Loads templates from files or uses built-in presets, compiles them,
and renders them with context from discovered files and git information.
"""
import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional  # Tuple not used here

# import json # Not used in this file
import pybars  # type: ignore
import structlog

from llmfiles.config import PromptConfig, OutputFormat, PresetTemplate
from llmfiles.exceptions import TemplateError
from llmfiles.util import get_language_hint

log = structlog.get_logger(__name__)

TRIPLE_BACKTICK = "```"
CDATA_START = "<![CDATA["
CDATA_END = "]]>"

# Default markdown template: general-purpose, suitable for many LLMs.
DEFAULT_MARKDOWN_TEMPLATE = rf"""
project root: {{{{project_path_header_display}}}}
{{{{#if show_absolute_project_path}}}}
(full absolute path: {{{{project_root_path_absolute}}}})
{{{{/if}}}}

{{{{#if source_tree}}}}
project structure:
{TRIPLE_BACKTICK}text
{{{{source_tree}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if files}}}}
files content:
{{{{#each files}}}}
---
file: {{{{this.relative_path}}}}
{{{{#if this.extension}}}}language hint: {{{{this.extension}}}}{{{{/if}}}}
content:
{{{{this.content}}}} {{!-- this.content is processed (line numbers, code blocks per config) --}}
---
{{{{/each}}}}
{{{{else}}}}
(no files included based on current filters or input.)
{{{{/if}}}}

{{{{#if git_diff}}}}
staged git diff:
{TRIPLE_BACKTICK}diff
{{{{git_diff}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if git_diff_branches}}}}
git diff ({{{{git_diff_branch_base}}}}...{{{{git_diff_branch_compare}}}}):
{TRIPLE_BACKTICK}diff
{{{{git_diff_branches}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if git_log_branches}}}}
git log ({{{{git_log_branch_base}}}}...{{{{git_log_branch_compare}}}}):
{TRIPLE_BACKTICK}text
{{{{git_log_branches}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if user_vars}}}}
user variables:
{TRIPLE_BACKTICK}text
{{{{#each user_vars}}}}
- {{{{ @key }}}}: {{{{this}}}}
{{{{/each}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}
"""

# Default XML template: a generic XML structure.
DEFAULT_XML_TEMPLATE = rf"""
<llmfiles_context generated_at_utc="{{{{now}}}}">
    <project name="{CDATA_START}{{{{project_root_display_name}}}}{CDATA_END}" path_in_header="{CDATA_START}{{{{project_path_header_display}}}}{CDATA_END}" absolute_path="{CDATA_START}{{{{project_root_path_absolute}}}}{CDATA_END}" />
    {{{{#if source_tree}}}}<source_tree>{CDATA_START}{{{{source_tree}}}}{CDATA_END}</source_tree>{{{{/if}}}}
    <files{{{{#unless files}}}} count="0" message="no files were included."{{{{/unless}}}}>
    {{{{#each files}}}}
        <file relative_path="{{{{this.relative_path}}}}" extension="{{{{this.extension}}}}">
            <processed_content>{CDATA_START}{{{{this.content}}}}{CDATA_END}</processed_content>
            <raw_content>{CDATA_START}{{{{this.raw_content}}}}{CDATA_END}</raw_content>
        </file>
    {{{{/each}}}}
    </files>
    {{{{#if git_diff}}}}<git_info type="staged_diff">{CDATA_START}{{{{git_diff}}}}{CDATA_END}</git_info>{{{{/if}}}}
    {{{{#if git_diff_branches}}}}<git_info type="branch_diff" base="{{{{git_diff_branch_base}}}}" compare="{{{{git_diff_branch_compare}}}}">{CDATA_START}{{{{git_diff_branches}}}}{CDATA_END}</git_info>{{{{/if}}}}
    {{{{#if git_log_branches}}}}<git_info type="branch_log" base="{{{{git_log_branch_base}}}}" compare="{{{{git_log_branch_compare}}}}">{CDATA_START}{{{{git_log_branches}}}}{CDATA_END}</git_info>{{{{/if}}}}
    {{{{#if user_vars}}}}<user_variables>
    {{{{#each user_vars}}}}<variable key="{{{{@key}}}}">{CDATA_START}{{{{this}}}}{CDATA_END}</variable>{{{{/each}}}}
    </user_variables>{{{{/if}}}}
</llmfiles_context>
"""

# Preset template for Anthropic Claude models. Uses `raw_content`.
PRESET_CLAUDE_OPTIMAL_TEMPLATE = rf"""
<documents project_context_display_path="{CDATA_START}{{{{project_path_header_display}}}}{CDATA_END}" project_name="{CDATA_START}{{{{project_root_display_name}}}}{CDATA_END}">
{{{{#each files}}}}
<document index="{{{{add @index 1}}}}">
<source_filename>{CDATA_START}{{{{this.relative_path}}}}{CDATA_END}</source_filename>
<document_content>{CDATA_START}{{{{this.raw_content}}}}{CDATA_END}</document_content>
</document>
{{{{/each}}}}
{{{{#if source_tree}}}}<document index="{{{{claude_indices.source_tree_idx}}}}"><source_filename>project structure ({{{{project_root_display_name}}}}) a</source_filename><document_content>{CDATA_START}{{{{source_tree}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if git_diff}}}}<document index="{{{{claude_indices.git_diff_idx}}}}"><source_filename>staged git diff ({{{{project_root_display_name}}}}) b</source_filename><document_content>{CDATA_START}{{{{git_diff}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if git_diff_branches}}}}<document index="{{{{claude_indices.git_diff_branches_idx}}}}"><source_filename>git diff ({{{{git_diff_branch_base}}}}...{{{{git_diff_branch_compare}}}}) for {{{{project_root_display_name}}}} c</source_filename><document_content>{CDATA_START}{{{{git_diff_branches}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if git_log_branches}}}}<document index="{{{{claude_indices.git_log_branches_idx}}}}"><source_filename>git log ({{{{git_log_branch_base}}}}...{{{{git_log_branch_compare}}}}) for {{{{project_root_display_name}}}} d</source_filename><document_content>{CDATA_START}{{{{git_log_branches}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if user_vars}}}}<document index="{{{{claude_indices.user_vars_idx}}}}"><source_filename>user defined variables ({{{{project_root_display_name}}}}) e</source_filename><document_content>{CDATA_START}{{#each user_vars}}{{{{@key}}}}: {{{{this}}}}\n{{/each}}{CDATA_END}</document_content></document>{{{{/if}}}}
</documents>
"""


def _add_helper_for_pybars(_this_context: Any, *args: Any) -> float:
    """Pybars helper to sum numeric arguments."""
    return sum(num_arg for num_arg in args if isinstance(num_arg, (int, float)))


def _now_utc_iso_helper_for_pybars(_this_context: Any, *args: Any) -> str:
    """Pybars helper for current UTC timestamp in ISO 8601 format."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class TemplateRenderer:
    """Manages loading, compilation, and rendering of Handlebars templates."""

    def __init__(self, config: PromptConfig):
        self.config = config
        self.handlebars_compiler = pybars.Compiler()
        self.registered_helpers = {
            "add": _add_helper_for_pybars,
            "now": _now_utc_iso_helper_for_pybars,
            "get_lang_hint": lambda _this, ext_str: get_language_hint(ext_str),
        }
        self.template_source_name: str = "unknown_source"
        self.raw_template_string: str = self._determine_and_load_template_string()

        try:
            self.compiled_template_function = self.handlebars_compiler.compile(
                self.raw_template_string
            )
        except Exception as e:
            raise TemplateError(
                f"Failed to compile template from '{self.template_source_name}': {e}"
            ) from e

    def _determine_and_load_template_string(self) -> str:
        """Loads template string based on config: custom file > preset > default format."""
        if self.config.template_path:
            self.template_source_name = f"custom_file:{self.config.template_path}"
            log.info("loading_custom_template", path=str(self.config.template_path))
            try:
                return self.config.template_path.read_text(encoding="utf-8")
            except Exception as e:
                raise TemplateError(
                    f"Failed to read template file {self.config.template_path}: {e}"
                ) from e

        if self.config.preset_template:
            self.template_source_name = f"preset:{self.config.preset_template.value}"
            log.info("using_preset_template", name=self.config.preset_template.value)
            preset_map = {
                PresetTemplate.DEFAULT: DEFAULT_MARKDOWN_TEMPLATE,
                PresetTemplate.CLAUDE_OPTIMAL: PRESET_CLAUDE_OPTIMAL_TEMPLATE,
                PresetTemplate.GENERIC_XML: DEFAULT_XML_TEMPLATE,
            }
            if self.config.preset_template in preset_map:
                return preset_map[self.config.preset_template]
            log.warning(
                "unknown_preset_template_falling_back",
                preset=self.config.preset_template.value,
            )

        self.template_source_name = f"default_format:{self.config.output_format.value}"
        log.info(
            "using_default_template_for_format", format=self.config.output_format.value
        )
        if self.config.output_format == OutputFormat.XML:
            return DEFAULT_XML_TEMPLATE
        if self.config.output_format == OutputFormat.JSON:
            # JSON output structure is primarily handled by cli.py's JSON serialization.
            # This template is a placeholder if a part of a larger JSON structure was templated.
            return "{{json_prompt_content}}"
        return DEFAULT_MARKDOWN_TEMPLATE

    def render(self, template_context_data: Dict[str, Any]) -> str:
        """Renders the compiled template with the given context data."""
        log.info("rendering_template", source=self.template_source_name)
        try:
            rendered_string = self.compiled_template_function(
                template_context_data, helpers=self.registered_helpers
            )
            log.debug(
                "template_rendered_successfully", source=self.template_source_name
            )
            return rendered_string.strip() + "\n"  # Ensure single trailing newline
        except Exception as e:
            log.error(
                "template_rendering_error",
                source=self.template_source_name,
                error=str(e),
                exc_info=True,
            )
            if isinstance(e, pybars.PybarsError) and "missing" in str(e).lower():
                raise TemplateError(
                    f"Render fail for '{self.template_source_name}': variable missing: {e}"
                ) from e
            raise TemplateError(
                f"Render fail for '{self.template_source_name}': {e}"
            ) from e


def _build_tree_string(file_entries: List[Dict[str, Any]], config: PromptConfig) -> str:
    """Generates a text-based directory tree string from file_entries."""
    if not file_entries:
        return "(no files included for tree.)"

    tree_structure: Dict[str, Any] = {}
    for entry_dict in file_entries:
        relative_path_str = entry_dict.get("relative_path")
        if not relative_path_str:
            log.warning(
                "skipping_file_entry_no_relative_path_for_tree", entry=entry_dict
            )
            continue

        path_parts = Path(relative_path_str).parts
        current_dict_level = tree_structure
        for i, part_name in enumerate(path_parts):
            is_last_part = i == len(path_parts) - 1
            node_data = current_dict_level.setdefault(
                part_name, {"_type": "file" if is_last_part else "dir", "_children": {}}
            )
            if (
                not is_last_part and node_data["_type"] == "file"
            ):  # Correct if wrongly assumed file
                node_data["_type"] = "dir"
            if not is_last_part:
                current_dict_level = node_data["_children"]

    def format_tree_node_recursively(
        node_dict_level: Dict[str, Any], indent_str: str = ""
    ) -> List[str]:
        output_lines: List[str] = []
        item_names_to_display = sorted(
            [name for name in node_dict_level if name not in ("_type", "_children")],
            key=lambda s: s.lower(),
        )

        for i, item_name in enumerate(item_names_to_display):
            item_data = node_dict_level[item_name]
            is_last_item_at_this_level = i == len(item_names_to_display) - 1
            connector_str = "└── " if is_last_item_at_this_level else "├── "
            output_lines.append(f"{indent_str}{connector_str}{item_name}")

            if item_data["_type"] == "dir" and item_data["_children"]:
                new_indent_str = indent_str + (
                    "    " if is_last_item_at_this_level else "│   "
                )
                output_lines.extend(
                    format_tree_node_recursively(item_data["_children"], new_indent_str)
                )
        return output_lines

    # Display name for the root of the tree. Uses project_root_display_name from context.
    # This function builds the tree *under* that root.
    tree_root_display_name = config.base_dir.name or str(
        config.base_dir
    )  # Fallback if root is '/'

    final_tree_lines = [
        f"{tree_root_display_name}/"
    ]  # Tree starts with the root directory name
    final_tree_lines.extend(format_tree_node_recursively(tree_structure))
    return "\n".join(final_tree_lines)


def build_template_context(
    config: PromptConfig,
    file_data_list: List[Dict[str, Any]],
    git_staged_diff: Optional[str],
    git_branch_diff: Optional[str],
    git_branch_log: Optional[str],
) -> Dict[str, Any]:
    """Constructs the context dictionary for rendering the Handlebars template."""
    log.info("building_template_context")
    if not (config.base_dir and config.base_dir.is_absolute()):
        raise TemplateError(
            "base_dir is not configured or not absolute for template context."
        )

    source_tree_str = _build_tree_string(file_data_list, config)
    source_tree_for_ctx = (
        source_tree_str
        if source_tree_str and source_tree_str != "(no files included for tree.)"
        else None
    )

    project_root_name = config.base_dir.name or str(config.base_dir)
    project_path_header_display = (
        str(config.base_dir) if config.show_absolute_project_path else project_root_name
    )

    claude_document_indices: Dict[str, int] = {}
    if config.preset_template == PresetTemplate.CLAUDE_OPTIMAL:
        idx_counter = len(file_data_list) + 1
        if source_tree_for_ctx:
            claude_document_indices["source_tree_idx"] = idx_counter
            idx_counter += 1
        if git_staged_diff:
            claude_document_indices["git_diff_idx"] = idx_counter
            idx_counter += 1
        if git_branch_diff:
            claude_document_indices["git_diff_branches_idx"] = idx_counter
            idx_counter += 1
        if git_branch_log:
            claude_document_indices["git_log_branches_idx"] = idx_counter
            idx_counter += 1
        if config.user_vars:
            claude_document_indices["user_vars_idx"] = idx_counter

    raw_context_dict: Dict[str, Any] = {
        "project_root_path_absolute": str(config.base_dir),
        "project_root_display_name": project_root_name,
        "project_path_header_display": project_path_header_display,
        "show_absolute_project_path": config.show_absolute_project_path,
        "source_tree": source_tree_for_ctx,
        "files": file_data_list or None,
        "git_diff": git_staged_diff or None,
        "git_diff_branches": git_branch_diff or None,
        "git_diff_branch_base": config.git_diff_branch
        if config.git_diff_branch and git_branch_diff
        else None,
        "git_diff_branch_compare": config.git_diff_branch
        if config.git_diff_branch and git_branch_diff
        else None,
        "git_log_branches": git_branch_log or None,
        "git_log_branch_base": config.git_log_branch
        if config.git_log_branch and git_branch_log
        else None,
        "git_log_branch_compare": config.git_log_branch
        if config.git_log_branch and git_branch_log
        else None,
        "user_vars": config.user_vars or None,
        "claude_indices": claude_document_indices or None,
    }

    final_context = {k: v for k, v in raw_context_dict.items() if v is not None}
    log.debug("template_context_built", keys=list(final_context.keys()))
    return final_context