# llmfiles/templating.py
"""Handlebars templating logic."""
import logging
import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import pybars  # Using pybars3 wrapper

from .config import PromptConfig, OutputFormat, PresetTemplate
from .exceptions import TemplateError

logger = logging.getLogger(__name__)

# Define constants for clarity
TRIPLE_BACKTICK = "```"
CDATA_START = "<![CDATA["
CDATA_END = "]]>"

# --- Default/Preset Templates ---
# Updated to use project_root_display_name
DEFAULT_MARKDOWN_TEMPLATE = rf"""
Project: {{{{ project_root_display_name }}}}

{{{{#if source_tree}}}}
Source Tree:
{TRIPLE_BACKTICK}text
{{{{{{ source_tree }}}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if files}}}}
Files:
{{{{#each files}}}}
`{{{{ this.relative_path }}}}`:
{{{{{{ this.content }}}}}}

{{{{/each}}}}
{{{{else}}}}
(No files included based on filters)
{{{{/if}}}}

{{{{#if git_diff}}}}
Staged Git Diff (HEAD vs Index):
{TRIPLE_BACKTICK}diff
{{{{{{ git_diff }}}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if git_diff_branches}}}}
Git Diff ({{{{ git_diff_branch_base }}}}..{{{{ git_diff_branch_compare }}}}):
{TRIPLE_BACKTICK}diff
{{{{{{ git_diff_branches }}}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if git_log_branches}}}}
Git Log ({{{{ git_log_branch_base }}}}..{{{{ git_log_branch_compare }}}}):
{TRIPLE_BACKTICK}text
{{{{{{ git_log_branches }}}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if user_vars}}}}
User Variables:
{{{{#each user_vars}}}}
- {{{{ @key }}}}: {{{{ this }}}}
{{{{/each}}}}
{{{{/if}}}}
"""

# Updated to use project_root_display_name and project_root_path_absolute
DEFAULT_XML_TEMPLATE = rf"""
<prompt_data>
    <project_name>{{{{ project_root_display_name }}}}</project_name>
    <project_root_path_absolute>{{{{ project_root_path_absolute }}}}</project_root_path_absolute>

    {{{{#if source_tree}}}}
    <source_tree>{CDATA_START}
{{{{{{ source_tree }}}}}}
    {CDATA_END}</source_tree>
    {{{{/if}}}}

    <files>
        {{{{#each files}}}}
        <file path="{{{{ this.relative_path }}}}">
            <content>{CDATA_START}
{{{{{{ this.content }}}}}}
            {CDATA_END}</content>
            <raw_content>{CDATA_START}
{{{{{{ this.raw_content }}}}}}
            {CDATA_END}</raw_content>
        </file>
        {{{{/each}}}}
    </files>

    {{{{#if git_diff}}}}
    <git_diff type="staged">{CDATA_START}
{{{{{{ git_diff }}}}}}
    {CDATA_END}</git_diff>
    {{{{/if}}}}

    {{{{#if git_diff_branches}}}}
    <git_diff type="branches" base="{{{{ git_diff_branch_base }}}}" compare="{{{{ git_diff_branch_compare }}}}">{CDATA_START}
{{{{{{ git_diff_branches }}}}}}
    {CDATA_END}</git_diff>
    {{{{/if}}}}

     {{{{#if git_log_branches}}}}
    <git_log type="branches" base="{{{{ git_log_branch_base }}}}" compare="{{{{ git_log_branch_compare }}}}">{CDATA_START}
{{{{{{ git_log_branches }}}}}}
    {CDATA_END}</git_log>
    {{{{/if}}}}

    {{{{#if user_vars}}}}
    <user_variables>
    {{{{#each user_vars}}}}
        <variable key="{{{{ @key }}}}">{{{{ this }}}}</variable>
    {{{{/each}}}}
    </user_variables>
    {{{{/if}}}}

</prompt_data>
"""

# (PRESET_CLAUDE_OPTIMAL_TEMPLATE remains the same as it defines its own document structure)
PRESET_CLAUDE_OPTIMAL_TEMPLATE = rf"""
<documents>
{{{{#each files}}}}
<document index="{{{{add @index 1}}}}">
<source>{{{{this.relative_path}}}}</source>
<document_content>{CDATA_START}
{{{{{{this.raw_content}}}}}}
{CDATA_END}</document_content>
</document>
{{{{/each}}}}

{{{{#if source_tree}}}}
<document index="{{{{claude_indices.source_tree_idx}}}}">
<source>Project Structure ({{project_root_display_name}})</source>
<document_content>{CDATA_START}
{{{{{{source_tree}}}}}}
{CDATA_END}</document_content>
</document>
{{{{/if}}}}

{{{{#if git_diff}}}}
<document index="{{{{claude_indices.git_diff_idx}}}}" type="staged_diff">
<source>Staged Git Diff (HEAD vs Index) for {{project_root_display_name}}</source>
<document_content>{CDATA_START}
{{{{{{git_diff}}}}}}
{CDATA_END}</document_content>
</document>
{{{{/if}}}}

{{{{#if git_diff_branches}}}}
<document index="{{{{claude_indices.git_diff_branches_idx}}}}" type="branch_diff" base="{{{{git_diff_branch_base}}}}" compare="{{{{git_diff_branch_compare}}}}">
<source>Git Diff ({{{{git_diff_branch_base}}}}..{{{{git_diff_branch_compare}}}}) for {{project_root_display_name}}</source>
<document_content>{CDATA_START}
{{{{{{git_diff_branches}}}}}}
{CDATA_END}</document_content>
</document>
{{{{/if}}}}

{{{{#if git_log_branches}}}}
<document index="{{{{claude_indices.git_log_branches_idx}}}}" type="branch_log" base="{{{{git_log_branch_base}}}}" compare="{{{{git_log_branch_compare}}}}">
<source>Git Log ({{{{git_log_branch_base}}}}..{{{{git_log_branch_compare}}}}) for {{project_root_display_name}}</source>
<document_content>{CDATA_START}
{{{{{{git_log_branches}}}}}}
{CDATA_END}</document_content>
</document>
{{{{/if}}}}

{{{{#if user_vars}}}}
<document index="{{{{claude_indices.user_vars_idx}}}}" type="user_variables">
<source>User Variables for {{project_root_display_name}}</source>
<document_content>{CDATA_START}
{{{{#each user_vars}}}}
{{{{@key}}}}: {{{{this}}}}
{{{{/each}}}}
{CDATA_END}</document_content>
</document>
{{{{/if}}}}

</documents>
"""


# --- Handlebars Helpers ---
def _add_helper(_this, *args):
    """Simple addition helper."""
    numeric_args = [arg for arg in args if isinstance(arg, (int, float))]
    return sum(numeric_args)


# --- Template Renderer Class ---
class TemplateRenderer:
    """Handles loading and rendering Handlebars templates."""
    def __init__(self, config: PromptConfig):
        self.config = config
        self.handlebars = pybars.Compiler()
        self.helpers = {
            "add": _add_helper,
            "now": lambda _this, *args: datetime.datetime.now().isoformat(),
        }
        self.template_content = self._load_template()
        try:
            self.template = self.handlebars.compile(self.template_content)
        except Exception as e:
            raise TemplateError(f"Failed to compile template: {e}")

    def _load_template(self) -> str:
        """Loads template content based on config priority."""
        if self.config.template_path:
            logger.info(f"Loading custom template from: {self.config.template_path}")
            try:
                return self.config.template_path.read_text(encoding="utf-8")
            except Exception as e:
                raise TemplateError(
                    f"Failed to read template file {self.config.template_path}: {e}"
                )
        elif self.config.preset_template:
            logger.info(f"Using preset template: {self.config.preset_template.value}")
            if self.config.preset_template == PresetTemplate.CLAUDE_OPTIMAL:
                return PRESET_CLAUDE_OPTIMAL_TEMPLATE
            elif self.config.preset_template == PresetTemplate.GENERIC_XML:
                return DEFAULT_XML_TEMPLATE
            elif self.config.preset_template == PresetTemplate.DEFAULT:
                return DEFAULT_MARKDOWN_TEMPLATE
            else:
                logger.warning(
                    f"Unknown preset template '{self.config.preset_template.value}', falling back to default markdown."
                )
                return DEFAULT_MARKDOWN_TEMPLATE
        else:
            logger.info(
                f"Using default template for format: {self.config.output_format.value}"
            )
            if self.config.output_format == OutputFormat.XML:
                return DEFAULT_XML_TEMPLATE
            return DEFAULT_MARKDOWN_TEMPLATE

    def render(self, context: Dict[str, Any]) -> str:
        """Renders the loaded template with the given context."""
        logger.info("Rendering template...")
        try:
            rendered = self.template(context, helpers=self.helpers)
            logger.info("Template rendered successfully.")
            return rendered.strip() + "\n"
        except Exception as e:
            logger.error(f"Template rendering error: {e}")
            logger.error(f"Context keys available: {list(context.keys())}")
            raise TemplateError(f"Failed to render template: {e}")


# --- Tree Building ---
def _build_tree_string(file_data: List[Dict[str, Any]], config: PromptConfig) -> str:
    """Generates a text-based directory tree string."""
    if not file_data:
        return "(No files included)"

    tree: Dict[str, Any] = {}
    base_dir = config.base_dir
    base_name = base_dir.name if base_dir and base_dir.name else str(base_dir)

    for item in file_data:
        try:
            relative_path = Path(item.get("relative_path", ""))
            if not relative_path.parts:
                continue

            parts = list(relative_path.parts)
            current_level = tree
            for i, part in enumerate(parts):
                is_last_part = i == len(parts) - 1
                if part not in current_level:
                    current_level[part] = {
                        "_type": "file" if is_last_part else "dir",
                        "_children": {},
                    }
                if not is_last_part and current_level[part]["_type"] == "file":
                    current_level[part]["_type"] = "dir"
                if not is_last_part:
                    if "_children" not in current_level[part]:
                        current_level[part]["_children"] = {}
                    current_level = current_level[part]["_children"]
        except Exception as e:
            logger.warning(
                f"Error processing path for tree generation '{item.get('relative_path')}': {e}"
            )

    def format_tree(
        node: Dict[str, Any], indent: str = "", is_last_level: bool = True
    ) -> List[str]:
        lines = []
        sorted_keys = sorted([k for k in node if not k.startswith("_")])

        for i, key in enumerate(sorted_keys):
            item = node[key]
            is_current_item_last = i == len(sorted_keys) - 1
            connector = "└── " if is_current_item_last else "├── "
            lines.append(f"{indent}{connector}{key}")

            if item["_type"] == "dir":
                new_indent = indent + ("    " if is_current_item_last else "│   ")
                lines.extend(
                    format_tree(
                        item.get("_children", {}), new_indent, is_current_item_last
                    )
                )
        return lines

    tree_lines = [base_name + "/"]
    tree_lines.extend(format_tree(tree))
    return "\n".join(tree_lines)


# --- Context Building ---
def build_template_context(
    config: PromptConfig,
    file_data: List[Dict[str, Any]],
    git_diff: Optional[str],
    git_diff_branches_data: Optional[str],
    git_log_branches_data: Optional[str],
) -> Dict[str, Any]:
    """Builds the context dictionary for Handlebars rendering."""
    logger.info("Building template context...")

    if not config.base_dir or not config.base_dir.is_absolute():
        raise TemplateError(
            "Base directory is not configured or resolved for context building."
        )

    source_tree_str = _build_tree_string(file_data, config)

    project_display_name = (
        config.base_dir.name
        if config.base_dir and config.base_dir.name
        else str(config.base_dir)
    )

    claude_indices = {}
    if config.preset_template == PresetTemplate.CLAUDE_OPTIMAL:
        idx = len(file_data) + 1
        has_tree = source_tree_str and source_tree_str != "(No files included)"
        if has_tree:
            claude_indices["source_tree_idx"] = idx
            idx += 1
        if git_diff:
            claude_indices["git_diff_idx"] = idx
            idx += 1
        if git_diff_branches_data:
            claude_indices["git_diff_branches_idx"] = idx
            idx += 1
        if git_log_branches_data:
            claude_indices["git_log_branches_idx"] = idx
            idx += 1
        if config.user_vars:
            claude_indices["user_vars_idx"] = idx

    context = {
        "project_root_path_absolute": str(config.base_dir),  # Full absolute path
        "project_root_display_name": project_display_name,  # Just the directory name
        "source_tree": source_tree_str
        if source_tree_str != "(No files included)"
        else None,
        "files": file_data,
        "git_diff": git_diff,
        "git_diff_branches": git_diff_branches_data,
        "git_log_branches": git_log_branches_data,
        "user_vars": config.user_vars if config.user_vars else None,
        "git_diff_branch_base": config.git_diff_branch[0]
        if config.git_diff_branch
        else None,
        "git_diff_branch_compare": config.git_diff_branch[1]
        if config.git_diff_branch
        else None,
        "git_log_branch_base": config.git_log_branch[0]
        if config.git_log_branch
        else None,
        "git_log_branch_compare": config.git_log_branch[1]
        if config.git_log_branch
        else None,
        "claude_indices": claude_indices if claude_indices else None,
    }

    context = {
        k: v
        for k, v in context.items()
        if v is not None and (not isinstance(v, (list, dict)) or v)
    }
    logger.debug("Template context built.")
    return context