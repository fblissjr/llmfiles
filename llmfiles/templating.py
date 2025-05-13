# llmfiles/templating.py
"""
handles handlebars templating for generating the final prompt output.
loads templates from files or uses built-in presets, compiles them,
and renders them with context from discovered files and git information.
"""
import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import json
import pybars  # type: ignore # handlebars templating engine
import structlog  # for structured logging

from llmfiles.config import PromptConfig, OutputFormat, PresetTemplate
from llmfiles.exceptions import TemplateError
from llmfiles.util import get_language_hint  # for language hints in default templates

log = structlog.get_logger(__name__)  # module-level logger

# --- constants for common template elements ---
TRIPLE_BACKTICK = "```"
CDATA_START = "<![CDATA["
CDATA_END = "]]>"

# --- default/preset template strings ---
# these are raw handlebars templates.
# default markdown template: general-purpose, suitable for many llms.
DEFAULT_MARKDOWN_TEMPLATE = rf"""
project root: {{{{project_root_display_name}}}}
(full path: {{{{project_root_path_absolute}}}})

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

{{!-- git information sections, only shown if data exists --}}
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

# default xml template: a generic xml structure.
DEFAULT_XML_TEMPLATE = rf"""
<llmfiles_context generated_at_utc="{{{{now}}}}">
    <project name="{CDATA_START}{{{{project_root_display_name}}}}{CDATA_END}" absolute_path="{CDATA_START}{{{{project_root_path_absolute}}}}{CDATA_END}" />
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

# preset template for anthropic claude models. uses `raw_content`.
PRESET_CLAUDE_OPTIMAL_TEMPLATE = rf"""
<documents>
{{{{#each files}}}}
<document index="{{{{add @index 1}}}}">
<source_filename>{{{{this.relative_path}}}}</source_filename>
<document_content>{CDATA_START}{{{{this.raw_content}}}}{CDATA_END}</document_content>
</document>
{{{{/each}}}}
{{{{#if source_tree}}}}<document index="{{{{claude_indices.source_tree_idx}}}}"><source_filename>project structure ({{{{project_root_display_name}}}})</source_filename><document_content>{CDATA_START}{{{{source_tree}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if git_diff}}}}<document index="{{{{claude_indices.git_diff_idx}}}}"><source_filename>staged git diff ({{{{project_root_display_name}}}})</source_filename><document_content>{CDATA_START}{{{{git_diff}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if git_diff_branches}}}}<document index="{{{{claude_indices.git_diff_branches_idx}}}}"><source_filename>git diff ({{{{git_diff_branch_base}}}}...{{{{git_diff_branch_compare}}}}) for {{{{project_root_display_name}}}}</source_filename><document_content>{CDATA_START}{{{{git_diff_branches}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if git_log_branches}}}}<document index="{{{{claude_indices.git_log_branches_idx}}}}"><source_filename>git log ({{{{git_log_branch_base}}}}...{{{{git_log_branch_compare}}}}) for {{{{project_root_display_name}}}}</source_filename><document_content>{CDATA_START}{{{{git_log_branches}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if user_vars}}}}<document index="{{{{claude_indices.user_vars_idx}}}}"><source_filename>user defined variables ({{{{project_root_display_name}}}})</source_filename><document_content>{CDATA_START}{{#each user_vars}}{{{{@key}}}}: {{{{this}}}}\n{{/each}}{CDATA_END}</document_content></document>{{{{/if}}}}
</documents>
"""

# --- handlebars helper functions ---
def _add_helper_for_pybars(_this_context: Any, *args: Any) -> float:
    """pybars helper to sum numeric arguments. non-numeric args are ignored."""
    return sum(num_arg for num_arg in args if isinstance(num_arg, (int, float)))

def _now_utc_iso_helper_for_pybars(_this_context: Any, *args: Any) -> str:
    """pybars helper to output the current timestamp in iso 8601 format (utc)."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

class TemplateRenderer:
    """manages loading, compilation, and rendering of handlebars templates."""
    def __init__(self, config: PromptConfig):
        self.config = config
        self.handlebars_compiler = pybars.Compiler()  # handlebars compiler instance
        self.registered_helpers = {  # helpers available within templates
            "add": _add_helper_for_pybars,
            "now": _now_utc_iso_helper_for_pybars,
            "get_lang_hint": lambda _this, ext_str: get_language_hint(ext_str),
        }
        self.template_source_name: str = "unknown_source"  # for logging/error messages
        self.raw_template_string: str = self._determine_and_load_template_string()

        try:  # compile the template string for efficient re-use.
            self.compiled_template_function = self.handlebars_compiler.compile(
                self.raw_template_string
            )
        except (
            Exception
        ) as e:  # pybars can raise various errors for invalid template syntax.
            raise TemplateError(
                f"failed to compile template from '{self.template_source_name}': {e}"
            ) from e

    def _determine_and_load_template_string(self) -> str:
        """
        loads template string based on config priority: custom file > preset > default format.
        also sets `self.template_source_name` for context in logs/errors.
        """
        if (
            self.config.template_path
        ):  # highest priority: user-provided custom template file.
            self.template_source_name = f"custom_file:{self.config.template_path}"
            log.info("loading_custom_template", path=str(self.config.template_path))
            try:
                return self.config.template_path.read_text(encoding="utf-8")
            except Exception as e:
                raise TemplateError(
                    f"failed to read template file {self.config.template_path}: {e}"
                ) from e

        if self.config.preset_template:  # next priority: built-in preset template.
            self.template_source_name = f"preset:{self.config.preset_template.value}"
            log.info(
                "using_preset_template", preset_name=self.config.preset_template.value
            )
            preset_map = {
                PresetTemplate.DEFAULT: DEFAULT_MARKDOWN_TEMPLATE,
                PresetTemplate.CLAUDE_OPTIMAL: PRESET_CLAUDE_OPTIMAL_TEMPLATE,
                PresetTemplate.GENERIC_XML: DEFAULT_XML_TEMPLATE,
            }
            if self.config.preset_template in preset_map:
                return preset_map[self.config.preset_template]
            log.warning(
                "unknown_preset_template_fallback",
                preset=self.config.preset_template.value,
            )
            # if unknown preset, fall through to default format based template.

        # fallback: default template based on `config.output_format`.
        self.template_source_name = f"default_format:{self.config.output_format.value}"
        log.info(
            "using_default_template_for_format", format=self.config.output_format.value
        )
        if self.config.output_format == OutputFormat.XML:
            return DEFAULT_XML_TEMPLATE
        if self.config.output_format == OutputFormat.JSON:
            # json output structure is handled by cli.py, not a handlebars template for the entire json.
            # this is a placeholder if direct rendering of a "json prompt part" were needed.
            return "{{prompt_content_for_json_field}}"  # expecting 'prompt_content_for_json_field' in context
        return DEFAULT_MARKDOWN_TEMPLATE  # default to markdown.

    def render(self, template_context_data: Dict[str, Any]) -> str:
        """renders the compiled template with the given context data."""
        log.info("rendering_template", source=self.template_source_name)
        try:
            rendered_string = self.compiled_template_function(
                template_context_data, helpers=self.registered_helpers
            )
            log.debug(
                "template_rendered_successfully", source=self.template_source_name
            )
            return (
                rendered_string.strip() + "\n"
            )  # ensure single trailing newline for consistency.
        except Exception as e:  # pybars can raise errors if context variables are missing or helpers fail.
            log.error(
                "template_rendering_error",
                source=self.template_source_name,
                context_keys=list(template_context_data.keys()),
                error=str(e),
                exc_info=True,
            )
            if (
                isinstance(e, pybars.PybarsError) and "missing" in str(e).lower()
            ):  # more specific error for missing vars
                raise TemplateError(
                    f"render fail for '{self.template_source_name}': variable missing or access error: {e}"
                ) from e
            raise TemplateError(
                f"render fail for '{self.template_source_name}': {e}"
            ) from e


# --- directory tree building logic ---
def _build_tree_string(file_entries: List[Dict[str, Any]], config: PromptConfig) -> str:
    """generates a text-based directory tree string from `file_entries`."""
    if not file_entries:
        return "(no files included for tree.)"

    # tree_structure is a nested dict: `name -> {'_type': 'file'/'dir', '_children': {}}`
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
            is_last_part = (
                i == len(path_parts) - 1
            )  # is this part a file or a dir segment?
            node_data = current_dict_level.setdefault(
                part_name, {"_type": "file" if is_last_part else "dir", "_children": {}}
            )
            # if a path segment was first assumed to be a file but later found to be a directory.
            if not is_last_part and node_data["_type"] == "file":
                node_data["_type"] = "dir"
            if not is_last_part:
                current_dict_level = node_data["_children"]  # descend into children

    # recursive helper to format the `tree_structure` dict into display lines.
    def format_tree_node_recursively(
        node_dict_level: Dict[str, Any], indent_str: str = ""
    ) -> List[str]:
        output_lines: List[str] = []
        # sort items by name for consistent tree display.
        item_names_to_display = [
            name for name in node_dict_level if name not in ("_type", "_children")
        ]
        sorted_item_names = sorted(
            item_names_to_display, key=lambda s: s.lower()
        )  # Sort case-insensitively

        for i, item_name in enumerate(sorted_item_names):
            item_data = node_dict_level[item_name]
            is_last_item_at_this_level = i == len(sorted_item_names) - 1
            connector_str = "└── " if is_last_item_at_this_level else "├── "
            output_lines.append(f"{indent_str}{connector_str}{item_name}")

            if (
                item_data["_type"] == "dir" and item_data["_children"]
            ):  # if it's a dir with children, recurse.
                new_indent_str = indent_str + (
                    "    " if is_last_item_at_this_level else "│   "
                )
                output_lines.extend(
                    format_tree_node_recursively(item_data["_children"], new_indent_str)
                )
        return output_lines

    # determine the root name for the tree display.
    tree_root_display_name = (
        f"{config.base_dir.name}/" if config.base_dir.name else f"{config.base_dir}/"
    )

    final_tree_lines = [tree_root_display_name]
    final_tree_lines.extend(format_tree_node_recursively(tree_structure))
    return "\n".join(final_tree_lines)

# --- context building function for templates ---
def build_template_context(
    config: PromptConfig,
    file_data_list: List[Dict[str, Any]],
    git_staged_diff: Optional[str],
    git_branch_diff: Optional[str],
    git_branch_log: Optional[str],
) -> Dict[str, Any]:
    """
    constructs the context dictionary passed to the handlebars template for rendering.
    omits top-level keys if their values are none to simplify `{{#if key}}` checks in templates.
    """
    log.info("building_template_context")
    if not (
        config.base_dir and config.base_dir.is_absolute()
    ):  # should be ensured by promptconfig
        raise TemplateError(
            "base_dir is not configured or not absolute for template context."
        )

    source_tree_str = _build_tree_string(file_data_list, config)
    # use none if tree is placeholder, for cleaner template logic.
    source_tree_for_ctx = (
        source_tree_str
        if source_tree_str and source_tree_str != "(no files included for tree.)"
        else None
    )

    project_root_name = config.base_dir.name or str(
        config.base_dir
    )  # fallback to full path if no name (e.g. root '/')

    # calculate indices for claude-optimal preset if it's active.
    claude_document_indices: Dict[str, int] = {}
    if config.preset_template == PresetTemplate.CLAUDE_OPTIMAL:
        idx_counter = len(file_data_list) + 1  # start indexing after file documents
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

    # assemble the raw context dictionary.
    raw_context_dict: Dict[str, Any] = {
        "project_root_path_absolute": str(config.base_dir),
        "project_root_display_name": project_root_name,
        "source_tree": source_tree_for_ctx,
        "files": file_data_list
        or None,  # pass none if list is empty for `{{#if files}}`
        "git_diff": git_staged_diff or None,
        "git_diff_branches": git_branch_diff or None,
        "git_log_branches": git_branch_log or None,
        "user_vars": config.user_vars or None,
        "claude_indices": claude_document_indices or None,  # only present if calculated
        # branch names for context if diff/log between branches is present
        "git_diff_branch_base": config.git_diff_branch[0]
        if config.git_diff_branch and git_branch_diff
        else None,
        "git_diff_branch_compare": config.git_diff_branch[1]
        if config.git_diff_branch and git_branch_diff
        else None,
        "git_log_branch_base": config.git_log_branch[0]
        if config.git_log_branch and git_branch_log
        else None,
        "git_log_branch_compare": config.git_log_branch[1]
        if config.git_log_branch and git_branch_log
        else None,
    }
    # filter out top-level keys that have none values for cleaner template logic.
    final_context = {k: v for k, v in raw_context_dict.items() if v is not None}

    log.debug("template_context_built", keys=list(final_context.keys()))
    return final_context