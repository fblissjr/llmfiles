# llmfiles/structured_processing/language_parsers/python_parser.py
"""
Tree-sitter based parsing and element extraction for Python files.
Produces "chunk" dictionaries for functions and classes.
"""
from pathlib import Path
from typing import Dict, Any, List, Optional
import os # For path manipulation if needed for FQN
import structlog

# Use the aliased ast_utils from the structured_processing package
from llmfiles.structured_processing import ast_utils as ts_utils

log = structlog.get_logger(__name__)
LANG_PYTHON = "python" # Internal language name

def _build_fqn_py(
    source_file_rel_path: str, 
    element_name: str, 
    class_name: Optional[str] = None,
    project_root_name: Optional[str] = None # Optional project root name for top-level FQN
) -> str:
    """Builds a Pythonic Fully Qualified Name."""
    path_parts = list(Path(source_file_rel_path).parts)
    
    # Remove .py extension and __init__
    if path_parts and path_parts[-1].endswith(".py"):
        path_parts[-1] = path_parts[-1][:-3]
    if path_parts and path_parts[-1] == "__init__":
        path_parts.pop()

    fqn_elements = []
    if project_root_name and project_root_name not in path_parts: # Avoid duplicating if part of path already
        # Only add if it's not implicitly part of the path (e.g. if paths are already relative to a src/project_name dir)
        # This logic might need adjustment based on how `source_file_rel_path` is derived.
        # For now, let's assume source_file_rel_path starts from *within* the project's main package.
        pass # fqn_elements.append(project_root_name) 

    fqn_elements.extend(part for part in path_parts if part) # Add module path parts
    if class_name:
        fqn_elements.append(class_name)
    fqn_elements.append(element_name)
    
    return ".".join(fqn_elements)


def _extract_py_signature_details(func_node: ts_utils.Node, content_bytes: bytes) -> Dict[str, Any]:
    """Extracts signature details (params, return type, async) from a Python function node."""
    sig: Dict[str, Any] = {"params": [], "return_type": None, "is_async": False}
    
    # Check for 'async' keyword
    # The Python grammar has 'async' as a direct child of function_definition if present.
    if func_node.children and func_node.children.type == 'async':
        sig["is_async"] = True

    parameters_node = ts_utils.find_named_child_by_field(func_node, "parameters")
    if parameters_node:
        for param_child in parameters_node.named_children:
            param_detail = {"name": "_unknown_", "type_hint": None, "default_value": None}
            if param_child.type == 'identifier': # simple param: x
                param_detail["name"] = ts_utils.get_node_text_from_bytes(param_child, content_bytes)
            elif param_child.type == 'typed_parameter': # param with type: x: int
                name_node = param_child.child_by_field_name("name") # tree-sitter python uses 'name' field
                type_node = param_child.child_by_field_name("type")
                if name_node: param_detail["name"] = ts_utils.get_node_text_from_bytes(name_node, content_bytes)
                if type_node: param_detail["type_hint"] = ts_utils.get_node_text_from_bytes(type_node, content_bytes)
            elif param_child.type == 'default_parameter': # param with default: x=0 or x:int=0
                name_node = param_child.child_by_field_name("name")
                type_node = param_child.child_by_field_name("type") # Might be None
                value_node = param_child.child_by_field_name("value")
                if name_node: param_detail["name"] = ts_utils.get_node_text_from_bytes(name_node, content_bytes)
                if type_node: param_detail["type_hint"] = ts_utils.get_node_text_from_bytes(type_node, content_bytes)
                if value_node: param_detail["default_value"] = ts_utils.get_node_text_from_bytes(value_node, content_bytes)
            # Handling *args, **kwargs, positional/keyword only markers
            elif param_child.type == 'list_splat_pattern': # *args
                name_node = param_child.named_child(0) # The identifier node for args
                param_detail["name"] = f"*{ts_utils.get_node_text_from_bytes(name_node, content_bytes)}" if name_node else "*args"
            elif param_child.type == 'dictionary_splat_pattern': # **kwargs
                name_node = param_child.named_child(0) # The identifier node for kwargs
                param_detail["name"] = f"**{ts_utils.get_node_text_from_bytes(name_node, content_bytes)}" if name_node else "**kwargs"
            elif param_child.type == '*': param_detail["name"] = "*" # Keyword-only args marker
            elif param_child.type == '/': param_detail["name"] = "/" # Positional-only args marker
            
            if param_detail["name"] != "_unknown_":
                sig["params"].append(param_detail)

    return_type_node = ts_utils.find_named_child_by_field(func_node, "return_type")
    if return_type_node:
        sig["return_type"] = ts_utils.get_node_text_from_bytes(return_type_node, content_bytes)
    
    return sig

def extract_python_elements(file_path: Path, project_root_path: Path, content_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parses a Python file using Tree-sitter and extracts functions and classes as "elements" (chunks).
    """
    elements: List[Dict[str, Any]] = []
    relative_file_path_str = str(file_path.relative_to(project_root_path))
    project_name_for_fqn = project_root_path.name # Use project dir name as a root for FQN

    ast_root_node = ts_utils.parse_code_to_ast(content_bytes, LANG_PYTHON)
    if not ast_root_node:
        log.warning("failed_to_parse_python_file_for_chunking", file=relative_file_path_str)
        return elements

    # Use tree-sitter queries to find functions and classes
    func_captures = ts_utils.run_ts_query("functions", LANG_PYTHON, ast_root_node)
    class_captures = ts_utils.run_ts_query("classes", LANG_PYTHON, ast_root_node)

    # Process functions
    for node, capture_name in func_captures:
        if capture_name == "function.definition": # The whole function_definition node
            func_def_node = node
            name_node = ts_utils.find_named_child_by_field(func_def_node, "name")
            func_name = ts_utils.get_node_text_from_bytes(name_node, content_bytes)
            if not func_name: continue

            body_node = ts_utils.find_named_child_by_field(func_def_node, "body")
            docstring = ts_utils.extract_python_docstring(body_node, content_bytes) if body_node else None
            
            element = {
                "file_path": relative_file_path_str,
                "element_type": "function",
                "name": func_name,
                "qualified_name": _build_fqn_py(relative_file_path_str, func_name, project_root_name=project_name_for_fqn),
                "language": LANG_PYTHON,
                "start_line": func_def_node.start_point + 1,
                "end_line": func_def_node.end_point + 1,
                "docstring": docstring,
                "signature_details": _extract_py_signature_details(func_def_node, content_bytes),
                "source_code": ts_utils.get_node_text_from_bytes(func_def_node, content_bytes),
            }
            elements.append(element)

    # Process classes (and their methods)
    for node, capture_name in class_captures:
        if capture_name == "class.definition": # The whole class_definition node
            class_def_node = node
            name_node = ts_utils.find_named_child_by_field(class_def_node, "name")
            class_name = ts_utils.get_node_text_from_bytes(name_node, content_bytes)
            if not class_name: continue

            body_node = ts_utils.find_named_child_by_field(class_def_node, "body")
            docstring = ts_utils.extract_python_docstring(body_node, content_bytes) if body_node else None
            class_fqn = _build_fqn_py(relative_file_path_str, class_name, project_root_name=project_name_for_fqn)

            class_element = {
                "file_path": relative_file_path_str,
                "element_type": "class",
                "name": class_name,
                "qualified_name": class_fqn,
                "language": LANG_PYTHON,
                "start_line": class_def_node.start_point + 1,
                "end_line": class_def_node.end_point + 1,
                "docstring": docstring,
                "source_code": ts_utils.get_node_text_from_bytes(class_def_node, content_bytes), # Includes methods
                "methods": [] # Placeholder for individual method elements if desired later
            }
            elements.append(class_element)

            # Optionally, extract methods as separate elements if needed, or keep them part of class source_code
            if body_node:
                method_nodes = ts_utils.run_ts_query("functions", LANG_PYTHON, body_node) # Find functions within class body
                for method_node_tuple in method_nodes:
                    method_node, method_capture_name = method_node_tuple
                    if method_capture_name == "function.definition":
                        method_name_node = ts_utils.find_named_child_by_field(method_node, "name")
                        method_name = ts_utils.get_node_text_from_bytes(method_name_node, content_bytes)
                        if not method_name: continue

                        method_body_node = ts_utils.find_named_child_by_field(method_node, "body")
                        method_docstring = ts_utils.extract_python_docstring(method_body_node, content_bytes) if method_body_node else None
                        
                        method_element = {
                            "file_path": relative_file_path_str,
                            "element_type": "method",
                            "name": method_name,
                            "qualified_name": _build_fqn_py(relative_file_path_str, method_name, class_name=class_name, project_root_name=project_name_for_fqn),
                            "class_name": class_name, # Parent class
                            "language": LANG_PYTHON,
                            "start_line": method_node.start_point + 1,
                            "end_line": method_node.end_point + 1,
                            "docstring": method_docstring,
                            "signature_details": _extract_py_signature_details(method_node, content_bytes),
                            "source_code": ts_utils.get_node_text_from_bytes(method_node, content_bytes),
                        }
                        # elements.append(method_element) # Add as separate element OR
                        class_element["methods"].append(method_element) # Nest under class

    log.debug("extracted_python_elements_from_file", file=relative_file_path_str, count=len(elements))
    return elements