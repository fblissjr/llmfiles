# llmfiles/templating.py
"""Handlebars templating logic."""
import logging
import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import pybars  # type: ignore

from .config import PromptConfig, OutputFormat, PresetTemplate
from .exceptions import TemplateError
from .util import get_language_hint

logger = logging.getLogger(__name__)

TRIPLE_BACKTICK, CDATA_START, CDATA_END = "```", "<![CDATA[", "]]>"

DEFAULT_MARKDOWN_TEMPLATE = rf"""
Project Root: {{{{project_root_display_name}}}}
(Full Path: {{{{project_root_path_absolute}}}})

{{{{#if source_tree}}}}
Project Structure:
{TRIPLE_BACKTICK}text
{{{{source_tree}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if files}}}}
Files Content:
{{{{#each files}}}}
---
File: {{{{this.relative_path}}}}
{{{{#if this.extension}}}}Language Hint: {{{{this.extension}}}}{{{{/if}}}}
Content:
{{{{this.content}}}} {{!-- Processed content (line numbers, code blocks etc.) --}}
---
{{{{/each}}}}
{{{{else}}}}
(No files included based on current filters or input.)
{{{{/if}}}}

{{!-- Git sections --}}
{{{{#if git_diff}}}}
Staged Git Diff:
{TRIPLE_BACKTICK}diff
{{{{git_diff}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}
{{{{#if git_diff_branches}}}}
Git Diff ({{{{git_diff_branch_base}}}}...{{{{git_diff_branch_compare}}}}):
{TRIPLE_BACKTICK}diff
{{{{git_diff_branches}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}
{{{{#if git_log_branches}}}}
Git Log ({{{{git_log_branch_base}}}}...{{{{git_log_branch_compare}}}}):
{TRIPLE_BACKTICK}text
{{{{git_log_branches}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}

{{{{#if user_vars}}}}
User Variables:
{TRIPLE_BACKTICK}text
{{{{#each user_vars}}}}
- {{{{ @key }}}}: {{{{this}}}}
{{{{/each}}}}
{TRIPLE_BACKTICK}
{{{{/if}}}}
"""

DEFAULT_XML_TEMPLATE = rf"""
<llmfiles_context generated_at_utc="{{{{now}}}}">
    <project name="{{{{project_root_display_name}}}}" absolute_path="{{{{project_root_path_absolute}}}}" />
    {{{{#if source_tree}}}}<source_tree>{CDATA_START}{{{{source_tree}}}}{CDATA_END}</source_tree>{{{{/if}}}}
    <files{{{{#unless files}}}} count="0" message="No files included."{{{{/unless}}}}>
    {{{{#each files}}}}
        <file relative_path="{{{{this.relative_path}}}}" extension="{{{{this.extension}}}}">
            <content>{CDATA_START}{{{{this.content}}}}{CDATA_END}</content>
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

PRESET_CLAUDE_OPTIMAL_TEMPLATE = rf"""
<documents>
{{{{#each files}}}}
<document index="{{{{add @index 1}}}}">
<source_filename>{{{{this.relative_path}}}}</source_filename>
<document_content>{CDATA_START}{{{{this.raw_content}}}}{CDATA_END}</document_content>
</document>
{{{{/each}}}}
{{{{#if source_tree}}}}<document index="{{{{claude_indices.source_tree_idx}}}}"><source_filename>Project Structure ({{{{project_root_display_name}}}})</source_filename><document_content>{CDATA_START}{{{{source_tree}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if git_diff}}}}<document index="{{{{claude_indices.git_diff_idx}}}}"><source_filename>Staged Git Diff ({{{{project_root_display_name}}}})</source_filename><document_content>{CDATA_START}{{{{git_diff}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if git_diff_branches}}}}<document index="{{{{claude_indices.git_diff_branches_idx}}}}"><source_filename>Git Diff ({{{{git_diff_branch_base}}}}...{{{{git_diff_branch_compare}}}}) for {{{{project_root_display_name}}}}</source_filename><document_content>{CDATA_START}{{{{git_diff_branches}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if git_log_branches}}}}<document index="{{{{claude_indices.git_log_branches_idx}}}}"><source_filename>Git Log ({{{{git_log_branch_base}}}}...{{{{git_log_branch_compare}}}}) for {{{{project_root_display_name}}}}</source_filename><document_content>{CDATA_START}{{{{git_log_branches}}}}{CDATA_END}</document_content></document>{{{{/if}}}}
{{{{#if user_vars}}}}<document index="{{{{claude_indices.user_vars_idx}}}}"><source_filename>User Variables ({{{{project_root_display_name}}}})</source_filename><document_content>{CDATA_START}{{#each user_vars}}{{{{@key}}}}: {{{{this}}}}\n{{/each}}{CDATA_END}</document_content></document>{{{{/if}}}}
</documents>
"""


def _add_helper(_this: Any, *args: Any) -> float:
    return sum(a for a in args if isinstance(a, (int, float)))


def _now_utc_iso(_this: Any, *args: Any) -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class TemplateRenderer:
    def __init__(self, config: PromptConfig):
        self.config = config
        self.compiler = pybars.Compiler()
        self.helpers = {
            "add": _add_helper,
            "now": _now_utc_iso,
            "get_lang_hint": lambda _t, e: get_language_hint(e),
        }
        self.source_id: str = "unknown"
        self.raw_tpl_content: str = self._load_content()
        try:
            self.compiled_tpl = self.compiler.compile(self.raw_tpl_content)
        except Exception as e:
            raise TemplateError(f"Failed to compile template '{self.source_id}': {e}")

    def _load_content(self) -> str:
        if self.config.template_path:
            self.source_id = f"file:{self.config.template_path}"
            try:
                return self.config.template_path.read_text("utf-8")
            except Exception as e:
                raise TemplateError(f"Failed to read template {self.source_id}: {e}")
        if self.config.preset_template:
            self.source_id = f"preset:{self.config.preset_template.value}"
            tpl_map = {
                PresetTemplate.DEFAULT: DEFAULT_MARKDOWN_TEMPLATE,
                PresetTemplate.CLAUDE_OPTIMAL: PRESET_CLAUDE_OPTIMAL_TEMPLATE,
                PresetTemplate.GENERIC_XML: DEFAULT_XML_TEMPLATE,
            }
            if self.config.preset_template in tpl_map:
                return tpl_map[self.config.preset_template]
            logger.warning(
                f"Unknown preset '{self.config.preset_template.value}', falling back."
            )

        self.source_id = f"default_fmt:{self.config.output_format.value}"
        if self.config.output_format == OutputFormat.XML:
            return DEFAULT_XML_TEMPLATE
        if self.config.output_format == OutputFormat.JSON:
            return "{{prompt_content}}"  # JSON structure built by CLI
        return DEFAULT_MARKDOWN_TEMPLATE  # Default to Markdown

    def render(self, context: Dict[str, Any]) -> str:
        logger.info(f"Rendering template '{self.source_id}'...")
        try:
            rendered = self.compiled_tpl(context, helpers=self.helpers)
            return rendered.strip() + "\n"  # Ensure single trailing newline
        except Exception as e:
            keys = list(context.keys())  # For easier debugging of context
            logger.error(
                f"Template render error for '{self.source_id}' with keys {keys}: {e}",
                exc_info=True,
            )
            if isinstance(e, pybars.PybarsError) and "Missing" in str(e):
                raise TemplateError(
                    f"Render fail for '{self.source_id}': Variable missing/access error: {e}"
                )
            raise TemplateError(f"Render fail for '{self.source_id}': {e}")


def _build_tree_string(file_data: List[Dict[str, Any]], config: PromptConfig) -> str:
    if not file_data:
        return "(No files included for tree.)"
    tree_dict: Dict[str, Any] = {}
    for entry in file_data:
        rel_path_str = entry.get("relative_path")
        if not rel_path_str:
            continue
        parts, current_level = Path(rel_path_str).parts, tree_dict
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            node = current_level.setdefault(
                part, {"_type": "file" if is_last else "dir", "_children": {}}
            )
            if not is_last and node["_type"] == "file":
                node["_type"] = "dir"  # Promote to dir if has children
            if not is_last:
                current_level = node["_children"]

    def format_recursive(node: Dict[str, Any], prefix: str = "") -> List[str]:
        lines, sorted_keys = [], sorted(k for k in node if not k.startswith("_"))
        for i, key in enumerate(sorted_keys):
            item, is_last_item = node[key], (i == len(sorted_keys) - 1)
            conn = "└── " if is_last_item else "├── "
            lines.append(f"{prefix}{conn}{key}")
            if item["_type"] == "dir" and item["_children"]:
                new_prefix = prefix + ("    " if is_last_item else "│   ")
                lines.extend(format_recursive(item["_children"], new_prefix))
        return lines

    root_name = (
        f"{config.base_dir.name}/" if config.base_dir.name else f"{config.base_dir}/"
    )
    return "\n".join([root_name] + format_recursive(tree_dict))


def build_template_context(
    config: PromptConfig,
    files: List[Dict[str, Any]],
    git_diff: Optional[str],
    git_diff_br: Optional[str],
    git_log_br: Optional[str],
) -> Dict[str, Any]:
    logger.info("Building template context...")
    if not (config.base_dir and config.base_dir.is_absolute()):
        raise TemplateError(
            "Base directory not configured or not absolute for context."
        )

    tree_str = _build_tree_string(files, config)
    root_display_name = config.base_dir.name or str(config.base_dir)

    claude_idx: Dict[str, int] = {}
    if config.preset_template == PresetTemplate.CLAUDE_OPTIMAL:
        current = len(files) + 1
        if tree_str and tree_str != "(No files included for tree.)":
            claude_idx["source_tree_idx"] = current
            current += 1
        if git_diff:
            claude_idx["git_diff_idx"] = current
            current += 1
        if git_diff_br:
            claude_idx["git_diff_branches_idx"] = current
            current += 1
        if git_log_br:
            claude_idx["git_log_branches_idx"] = current
            current += 1
        if config.user_vars:
            claude_idx["user_vars_idx"] = current

    raw_ctx = {
        "project_root_path_absolute": str(config.base_dir),
        "project_root_display_name": root_display_name,
        "source_tree": tree_str
        if tree_str != "(No files included for tree.)"
        else None,
        "files": files or None,  # Pass None if empty for cleaner {{#if files}}
        "git_diff": git_diff or None,
        "git_diff_branches": git_diff_br or None,
        "git_log_branches": git_log_br or None,
        "user_vars": config.user_vars or None,
        "claude_indices": claude_idx or None,
        "git_diff_branch_base": config.git_diff_branch[0]
        if config.git_diff_branch and git_diff_br
        else None,
        "git_diff_branch_compare": config.git_diff_branch[1]
        if config.git_diff_branch and git_diff_br
        else None,
        "git_log_branch_base": config.git_log_branch[0]
        if config.git_log_branch and git_log_br
        else None,
        "git_log_branch_compare": config.git_log_branch[1]
        if config.git_log_branch and git_log_br
        else None,
    }
    return {
        k: v for k, v in raw_ctx.items() if v is not None
    }  # Filter out None top-level keys