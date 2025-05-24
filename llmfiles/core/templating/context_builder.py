# llmfiles/core/templating/context_builder.py
"""
Builds the context dictionary required for rendering Handlebars templates.
Includes logic for generating the project source tree visualization.
"""
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
import structlog

from llmfiles.config.settings import PromptConfig, PresetTemplate # For type hints and logic
from llmfiles.exceptions import TemplateError # For raising errors

log = structlog.get_logger(__name__)

def _build_tree_from_elements(content_elements: List[Dict[str, Any]], config: PromptConfig) -> str:
    """Generates a text-based directory tree string from the 'file_path' of content_elements."""
    if not content_elements: return "(no content elements for tree view.)"

    unique_relative_file_paths: Set[Path] = set()
    for element in content_elements:
        file_path_str = element.get("file_path") # This path is relative to config.base_dir
        if file_path_str: 
            try:
                unique_relative_file_paths.add(Path(file_path_str))
            except TypeError: # Path constructor might fail if file_path_str is not path-like
                log.warning("invalid_file_path_in_element_for_tree", path_data=file_path_str)
                continue


    if not unique_relative_file_paths: return "(no valid file paths in elements to build tree.)"

    tree_structure_dict: Dict[str, Any] = {} 
    for rel_path_obj in sorted(list(unique_relative_file_paths), key=lambda p: str(p).lower()):
        current_dict_level = tree_structure_dict
        parts = rel_path_obj.parts
        for i, part_name in enumerate(parts):
            is_last_part_of_path = (i == len(parts) - 1)
            node_data = current_dict_level.setdefault(part_name, {"_type_": "file" if is_last_part_of_path else "dir", "_children_": {}})
            if not is_last_part_of_path and node_data["_type_"] == "file": 
                node_data["_type_"] = "dir" # Correct if a file and dir had same prefix name
            if not is_last_part_of_path:
                current_dict_level = node_data["_children_"] 
    
    def format_tree_nodes_recursively(node_dict_level: Dict[str, Any], indent_str: str = "") -> List[str]:
        output_lines: List[str] = []
        # Sort items by name (case-insensitive) for consistent display, excluding internal keys
        item_names_to_display = sorted(
            [name for name in node_dict_level if not name.startswith("_")], 
            key=str.lower 
        )

        for i, item_name in enumerate(item_names_to_display):
            item_data = node_dict_level[item_name]
            is_last_item_at_this_level = i == len(item_names_to_display) - 1
            connector_str = "└── " if is_last_item_at_this_level else "├── "
            output_lines.append(f"{indent_str}{connector_str}{item_name}")

            if item_data["_type_"] == "dir" and item_data["_children_"]: 
                new_indent_str = indent_str + ("    " if is_last_item_at_this_level else "│   ")
                output_lines.extend(format_tree_nodes_recursively(item_data["_children_"], new_indent_str))
        return output_lines

    tree_root_display_name = config.base_dir.name if config.base_dir.name else str(config.base_dir)
    
    final_tree_lines = [f"{tree_root_display_name}/"] 
    final_tree_lines.extend(format_tree_nodes_recursively(tree_structure_dict))
    return "\n".join(final_tree_lines)

def build_template_context(
    config: PromptConfig,
    content_elements_list: List[Dict[str, Any]], 
    git_staged_diff: Optional[str],
    git_branch_diff_output: Optional[str], 
    git_branch_log_output: Optional[str],
) -> Dict[str, Any]:
    """Constructs the context dictionary passed to the Handlebars template for rendering."""
    log.info("building_template_context_for_renderer", num_elements=len(content_elements_list))
    if not (config.base_dir and config.base_dir.is_absolute()):
        raise TemplateError("config.base_dir is not set or not absolute for template context.")

    source_tree_display = _build_tree_from_elements(content_elements_list, config)
    source_tree_for_context = None
    if source_tree_display and not source_tree_display.startswith("("): # Avoid placeholder messages
        source_tree_for_context = source_tree_display
    
    project_root_name_for_display = config.base_dir.name or str(config.base_dir)
    header_path_for_display = str(config.base_dir) if config.show_absolute_project_path else project_root_name_for_display

    claude_document_indices_map: Dict[str, int] = {}
    if config.preset_template == PresetTemplate.CLAUDE_OPTIMAL:
        current_claude_idx = len(content_elements_list) + 1 
        if source_tree_for_context: claude_document_indices_map["source_tree_idx"] = current_claude_idx; current_claude_idx += 1
        if git_staged_diff: claude_document_indices_map["git_diff_idx"] = current_claude_idx; current_claude_idx += 1
        if git_branch_diff_output: claude_document_indices_map["git_diff_branches_idx"] = current_claude_idx; current_claude_idx +=1
        if git_branch_log_output: claude_document_indices_map["git_log_branches_idx"] = current_claude_idx; current_claude_idx += 1
        if config.user_vars: claude_document_indices_map["user_vars_idx"] = current_claude_idx

    raw_context_data: Dict[str, Any] = {
        "project_root_path_absolute": str(config.base_dir),
        "project_root_display_name": project_root_name_for_display,
        "project_path_header_display": header_path_for_display, 
        "show_absolute_project_path": config.show_absolute_project_path, 
        "source_tree": source_tree_for_context,
        "content_elements": content_elements_list or None, 
        "git_diff": git_staged_diff or None,
        "git_diff_branches": git_branch_diff_output or None,
        "git_diff_branch_base": config.git_diff_branch[0] if config.git_diff_branch and git_branch_diff_output else None,
        "git_diff_branch_compare": config.git_diff_branch[1] if config.git_diff_branch and git_branch_diff_output else None,
        "git_log_branches": git_branch_log_output or None,
        "git_log_branch_base": config.git_log_branch[0] if config.git_log_branch and git_branch_log_output else None,
        "git_log_branch_compare": config.git_log_branch[1] if config.git_log_branch and git_branch_log_output else None,
        "user_vars": config.user_vars or None,
        "claude_indices": claude_document_indices_map or None,
    }
    
    final_template_context = {k: v for k, v in raw_context_data.items() if v is not None}
    log.debug("template_context_prepared_with_keys", keys=list(final_template_context.keys()))
    return final_template_context