# smart_prompt_builder/templating.py
"""Handlebars templating logic."""
import logging
import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import pybars # Using pybars3 wrapper

from .config import PromptConfig, OutputFormat
from .exceptions import TemplateError

logger = logging.getLogger(__name__)

# Define constants for clarity and to avoid literal triple backticks in templates
TRIPLE_BACKTICK = "```"
CDATA_START = "<![CDATA["
CDATA_END = "]]>"

# --- Default Templates ---
DEFAULT_MARKDOWN_TEMPLATE = rf"""
Project Path: {{ project_root_path }}

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

# Note: XML uses CDATA which correctly handles nested ```, so modification might not be strictly
# necessary, but using variables for consistency is okay too. Let's keep it simple for XML.
DEFAULT_XML_TEMPLATE = rf"""
<prompt_data>
    <project_root_path>{{{{ project_root_path }}}}</project_root_path>

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

class TemplateRenderer:
    """Handles loading and rendering Handlebars templates."""
    def __init__(self, config: PromptConfig):
        self.config = config
        self.compiler = pybars.Compiler()
        self.template_content = self._load_template()
        try:
            # Need to double Handlebars braces when using f-strings for Python variables
            # The raw f-string (`rf"""...""") handles backslashes literally,
            # and we escape handlebars braces {{ -> {{ doubling them }} -> }}
            self.template = self.compiler.compile(self.template_content)
        except Exception as e:
            raise TemplateError(f"Failed to compile template: {e}")

    def _load_template(self) -> str:
        """Loads template content from file or returns default."""
        if self.config.template_path:
            logger.info(f"Loading custom template from: {self.config.template_path}")
            try:
                return self.config.template_path.read_text(encoding="utf-8")
            except Exception as e:
                raise TemplateError(f"Failed to read template file {self.config.template_path}: {e}")
        else:
            logger.info(f"Using default template for format: {self.config.output_format.value}")
            if self.config.output_format == OutputFormat.XML:
                return DEFAULT_XML_TEMPLATE
            # Default to Markdown for JSON output format as well, structure handled later
            return DEFAULT_MARKDOWN_TEMPLATE

    def render(self, context: Dict[str, Any]) -> str:
        """Renders the loaded template with the given context."""
        logger.info("Rendering template...")
        try:
            # Add standard helpers if needed (e.g., for date formatting)
            helpers = {
                 'now': lambda _this, *args: datetime.datetime.now().isoformat()
            }
            rendered = self.template(context, helpers=helpers)
            logger.info("Template rendered successfully.")
            # Clean up potential extra newlines from template definition
            return rendered.strip() + "\n"
        except Exception as e:
            # Provide more context on render errors
            logger.error(f"Template rendering error: {e}")
            logger.error(f"Context keys available: {list(context.keys())}")
            # Consider logging partial context for debugging if safe
            raise TemplateError(f"Failed to render template: {e}")


def _build_tree_string(file_data: List[Dict[str, Any]], config: PromptConfig) -> str:
    """Generates a text-based directory tree string."""
    if not file_data:
        return "(No files included)"

    tree: Dict[str, Any] = {}
    base_dir = config.base_dir
    base_name = base_dir.name if base_dir.name else str(base_dir) # Handle root case like "/"

    for item in file_data:
        try:
            # Use relative_path for building the tree structure
            relative_path = Path(item.get('relative_path', ''))
            if not relative_path.parts: continue # Skip empty paths

            parts = list(relative_path.parts)
            current_level = tree
            for i, part in enumerate(parts):
                is_last_part = i == len(parts) - 1
                if part not in current_level:
                    # Store type (dir/file) to handle leaf nodes correctly
                    current_level[part] = {'_type': 'file' if is_last_part else 'dir', '_children': {}}
                # Update type if we previously thought it was a file but now see it as a dir part
                if not is_last_part and current_level[part]['_type'] == 'file':
                     current_level[part]['_type'] = 'dir'
                if not is_last_part:
                    # Ensure _children exists if transitioning from file to dir
                    if '_children' not in current_level[part]:
                        current_level[part]['_children'] = {}
                    current_level = current_level[part]['_children']

        except Exception as e:
            logger.warning(f"Error processing path for tree generation '{item.get('relative_path')}': {e}")


    def format_tree(node: Dict[str, Any], indent: str = "", is_last_level: bool = True) -> List[str]:
        lines = []
        # Sort keys for predictable order, ignore internal keys
        sorted_keys = sorted([k for k in node if not k.startswith('_')])

        for i, key in enumerate(sorted_keys):
            item = node[key]
            is_current_item_last = i == len(sorted_keys) - 1
            connector = "└── " if is_current_item_last else "├── "
            lines.append(f"{indent}{connector}{key}")

            # Only add children if it's a directory
            if item['_type'] == 'dir':
                new_indent = indent + ("    " if is_current_item_last else "│   ")
                lines.extend(format_tree(item.get('_children', {}), new_indent, is_current_item_last))
        return lines

    tree_lines = [base_name + "/"] # Start with root dir name
    tree_lines.extend(format_tree(tree))
    return "\n".join(tree_lines)


def build_template_context(
    config: PromptConfig,
    file_data: List[Dict[str, Any]],
    git_diff: Optional[str],
    git_diff_branches_data: Optional[str],
    git_log_branches_data: Optional[str]
) -> Dict[str, Any]:
    """Builds the context dictionary for Handlebars rendering."""
    logger.info("Building template context...")

    # Ensure base_dir is resolved before generating tree
    if not config.base_dir or not config.base_dir.is_absolute():
         raise TemplateError("Base directory is not configured or resolved.")

    source_tree_str = _build_tree_string(file_data, config)

    context = {
        "project_root_path": str(config.base_dir),
        "source_tree": source_tree_str,
        "files": file_data, # Already contains processed path, content, etc.
        "git_diff": git_diff,
        "git_diff_branches": git_diff_branches_data,
        "git_log_branches": git_log_branches_data,
        "user_vars": config.user_vars,
        # Add branch names if available
         "git_diff_branch_base": config.git_diff_branch[0] if config.git_diff_branch else None,
         "git_diff_branch_compare": config.git_diff_branch[1] if config.git_diff_branch else None,
         "git_log_branch_base": config.git_log_branch[0] if config.git_log_branch else None,
         "git_log_branch_compare": config.git_log_branch[1] if config.git_log_branch else None,
    }
    # Filter out None values from the context root for cleaner templates
    context = {k: v for k, v in context.items() if v is not None}
    logger.debug("Template context built.")
    return context