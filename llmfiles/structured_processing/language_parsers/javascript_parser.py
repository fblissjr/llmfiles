# llmfiles/structured_processing/language_parsers/javascript_parser.py
"""
Tree-sitter based parsing and element extraction for JavaScript files.
Produces "element" (chunk) dictionaries for functions, classes, and methods.
"""
from pathlib import Path
from typing import Dict, Any, List, Optional
import structlog

from llmfiles.structured_processing import ast_utils as ts_utils

log = structlog.get_logger(__name__)
LANG_JS = "javascript" # Internal language name matching ast_utils.LANG_CONFIG_TS key

def _build_fqn_js(
    source_file_rel_path: str, 
    element_name: Optional[str], 
    class_name: Optional[str] = None,
    project_root_name: Optional[str] = None # Similar to Python FQN builder
) -> str:
    """Builds a JavaScript-style Fully Qualified Name (path.to.module.ClassName.methodName or path.to.module.functionName)."""
    path_parts = list(Path(source_file_rel_path).parts)
    
    if path_parts and path_parts[-1].endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        path_parts[-1] = Path(path_parts[-1]).stem # Remove extension
    
    # Remove common leading directories like 'src', 'lib', 'app' if they are at the start of rel_path
    # This heuristic helps create more canonical module paths.
    if path_parts and path_parts[0] in ("src", "lib", "app", "source", "sources", "js", "javascript"):
        path_parts.pop(0)
    
    fqn_elements = []
    # if project_root_name and (not path_parts or project_root_name != path_parts[0]):
    # fqn_elements.append(project_root_name) # Optional: prefix with project name

    fqn_elements.extend(part for part in path_parts if part)
    if class_name:
        fqn_elements.append(class_name)
    if element_name: # Can be None for anonymous arrow functions directly assigned
        fqn_elements.append(element_name)
    else: # No element name (e.g. anonymous arrow function)
        if not class_name and not path_parts : # Truly anonymous at top level of an unnamed file?
             return "anonymous_element"
        # If part of class or module, the name might be implied by assignment or context
        # For now, if element_name is None, the FQN might just be up to class/module.

    return ".".join(filter(None, fqn_elements)) if fqn_elements else "global_scope_element"


def _extract_js_signature_details(func_node: ts_utils.Node, content_bytes: bytes) -> Dict[str, Any]:
    """Extracts signature details (params, async) from a JS function/method node."""
    sig: Dict[str, Any] = {"params": [], "is_async": False, "is_generator": False}

    # Check for 'async' keyword
    # For function_declaration, method_definition: `(function_declaration "async"? ...)`
    # For arrow_function: `(arrow_function "async"? ...)`
    if func_node.children and func_node.children[0].type == 'async':
        sig["is_async"] = True
    
    # Check for generator functions `function* name() {}`
    name_field_node = ts_utils.find_named_child_by_field(func_node, "name") # Identifier or generator_marker
    if name_field_node and name_field_node.type == 'generator_marker': # '*'
         sig["is_generator"] = True
    elif func_node.children: # Check if first child of name is generator marker (older grammar?)
        first_child_of_name = name_field_node.children[0] if name_field_node and name_field_node.children else None
        if first_child_of_name and first_child_of_name.type == 'generator_marker':
             sig["is_generator"] = True


    # Parameters node name can vary ('formal_parameters', 'parameter_list')
    # Using a general approach: find child that looks like parameter list
    params_node = ts_utils.find_named_child_by_field(func_node, "parameters") # Common field name in JS grammar
    if not params_node: # Fallback for arrow functions which might not have 'parameters' as a field name
        for child in func_node.children:
            if child.type == 'formal_parameters': # Common node type for parameter lists
                params_node = child
                break
    
    if params_node:
        for param_child in params_node.named_children:
            param_name = "_unknown_"
            # JS parameters can be: identifier, assignment_pattern (for defaults), object_pattern, array_pattern, rest_pattern
            if param_child.type == 'identifier':
                param_name = ts_utils.get_node_text_from_bytes(param_child, content_bytes)
            elif param_child.type == 'required_parameter': # Often wraps identifier and type in TS
                 name_node = ts_utils.find_named_child_by_field(param_child, "pattern") or param_child.child(0) # Heuristic
                 if name_node: param_name = ts_utils.get_node_text_from_bytes(name_node, content_bytes)
            elif param_child.type == 'optional_parameter':
                 name_node = ts_utils.find_named_child_by_field(param_child, "pattern") or param_child.child(0)
                 if name_node: param_name = ts_utils.get_node_text_from_bytes(name_node, content_bytes) + "?" # Indicate optional
            elif param_child.type == 'assignment_pattern': # param = default_value
                left_node = ts_utils.find_named_child_by_field(param_child, "left") # The actual param name/pattern
                if left_node: param_name = ts_utils.get_node_text_from_bytes(left_node, content_bytes)
            elif param_child.type == 'object_pattern' or param_child.type == 'array_pattern':
                param_name = ts_utils.get_node_text_from_bytes(param_child, content_bytes) # Destructuring, show whole pattern
            elif param_child.type == 'rest_parameter' or param_child.type == 'rest_pattern': # ...args
                # The actual name is often a child identifier of the rest_parameter/pattern
                name_identifier_node = param_child.child(0) if param_child.child_count > 0 and param_child.child(0).type == 'identifier' else None
                if not name_identifier_node and param_child.child_count > 1 and param_child.child(1).type == 'identifier': # for `... args` pattern where `...` is child(0)
                    name_identifier_node = param_child.child(1)

                if name_identifier_node:
                    param_name = "..." + ts_utils.get_node_text_from_bytes(name_identifier_node, content_bytes)
                else: # Fallback if structure is unexpected
                    param_name = ts_utils.get_node_text_from_bytes(param_child, content_bytes)


            if param_name != "_unknown_":
                # For JS/TS, type hints are complex and part of 'type_annotation' or within the pattern
                # This simplified signature doesn't deeply parse them for now.
                sig["params"].append({"name": param_name, "type_hint": None, "default_value": None})
    
    # JS doesn't have explicit return type syntax like Python/Rust in standard JS (TS does)
    # For TS, one would look for a 'type_annotation' child of the function/method node.
    # For now, we'll leave return_type as None for JS, or it can be inferred from JSDoc.
    sig["return_type"] = None 

    return sig

def _get_name_from_js_declaration(node: ts_utils.Node, content_bytes: bytes) -> Optional[str]:
    """Helper to get name from various JS function/class declaration types."""
    name_node: Optional[ts_utils.Node] = None
    if node.type in ("function_declaration", "class_declaration", "method_definition"):
        name_node = ts_utils.find_named_child_by_field(node, "name")
    elif node.type == "function": # function expression: `const a = function myFunc() {}` or `const a = function() {}`
        name_node = ts_utils.find_named_child_by_field(node, "name") # This is for named function expressions
    # For arrow functions or functions/classes in variable declarators, name is trickier
    # `const myFunc = () => {}` or `let MyClass = class {}`
    # The name is part of the parent VariableDeclarator or LexicalDeclaration
    # This function is for nodes that inherently carry a name.
    return ts_utils.get_node_text_from_bytes(name_node, content_bytes) if name_node else None


def extract_javascript_elements(file_path: Path, project_root_path: Path, content_bytes: bytes) -> List[Dict[str, Any]]:
    """Parses a JavaScript/TypeScript file and extracts functions, classes, methods."""
    elements: List[Dict[str, Any]] = []
    relative_file_path_str = str(file_path.relative_to(project_root_path))
    project_name_for_fqn = project_root_path.name

    ast_root_node = ts_utils.parse_code_to_ast(content_bytes, LANG_JS)
    if not ast_root_node:
        log.warning("failed_to_parse_javascript_file_for_chunking", file=relative_file_path_str)
        return elements

    # Using queries to find relevant nodes
    func_captures = ts_utils.run_ts_query("functions", LANG_JS, ast_root_node)
    class_captures = ts_utils.run_ts_query("classes", LANG_JS, ast_root_node)
    
    processed_node_ids = set() # To avoid double processing from multiple query matches

    # Process functions and methods
    for node, capture_name in func_captures:
        func_node = node # This is the definition node
        if func_node.id in processed_node_ids: continue
        processed_node_ids.add(func_node.id)

        element_type = "function" # Default
        func_name: Optional[str] = None
        class_context_name: Optional[str] = None # If it's a method

        if capture_name == "function.definition": # function_declaration or named function_expression
            func_name = _get_name_from_js_declaration(func_node, content_bytes)
        elif capture_name == "function.definition_anon_arrow": # arrow_function
            element_type = "arrow_function"
            # Name might be inferred from variable assignment (more complex, requires parent analysis)
            # For now, treat as anonymous or derive if part of assignment
            # Check if parent is a variable_declarator: `const myFunc = () => ...`
            if func_node.parent and ts_utils.is_node_of_type(func_node.parent, LANG_JS, "variable_declarator"):
                name_node = ts_utils.find_named_child_by_field(func_node.parent, "name")
                func_name = ts_utils.get_node_text_from_bytes(name_node, content_bytes)

        elif capture_name == "method.definition": # method_definition in a class
            element_type = "method"
            func_name = _get_name_from_js_declaration(func_node, content_bytes)
            # Find parent class name
            parent_class_node = func_node.parent # Body of class
            if parent_class_node and parent_class_node.parent and \
               ts_utils.is_node_of_type(parent_class_node.parent, LANG_JS, ("class_declaration", "class_expression")):
                class_name_node = ts_utils.find_named_child_by_field(parent_class_node.parent, "name")
                class_context_name = ts_utils.get_node_text_from_bytes(class_name_node, content_bytes)
        
        if not func_name and element_type != "arrow_function": # Arrow functions can be anonymous
            func_name = f"anonymous_{func_node.type}_{func_node.start_point[0]+1}"


        preceding_comments = ts_utils.get_preceding_doc_comments(func_node)
        docstring = ts_utils.extract_docstring_from_comment_nodes(preceding_comments, content_bytes)

        element = {
            "file_path": relative_file_path_str,
            "element_type": element_type,
            "name": func_name,
            "qualified_name": _build_fqn_js(relative_file_path_str, func_name, class_name=class_context_name, project_root_name=project_name_for_fqn),
            "class_name": class_context_name,
            "language": LANG_JS,
            "start_line": func_node.start_point[0] + 1,
            "end_line": func_node.end_point[0] + 1,
            "docstring": docstring,
            "signature_details": _extract_js_signature_details(func_node, content_bytes),
            "source_code": ts_utils.get_node_text_from_bytes(func_node, content_bytes),
        }
        elements.append(element)

    # Process classes
    for node, capture_name in class_captures:
        class_node = node # This is the definition node
        if class_node.id in processed_node_ids: continue
        # If a class contained methods processed above, the class node itself still needs to be an element
        # Methods are often extracted within the class context or can be listed as children.
        # For now, we will make the class a single element, its source_code includes methods.
        # A more granular approach could make methods separate elements *as well*.

        class_name = _get_name_from_js_declaration(class_node, content_bytes)
        if not class_name: # e.g. const MyClass = class { ... }
            if class_node.parent and ts_utils.is_node_of_type(class_node.parent, LANG_JS, "variable_declarator"):
                name_node = ts_utils.find_named_child_by_field(class_node.parent, "name")
                class_name = ts_utils.get_node_text_from_bytes(name_node, content_bytes)
            if not class_name:
                class_name = f"anonymous_class_{class_node.start_point[0]+1}"
        
        processed_node_ids.add(class_node.id) # Mark class node as processed

        preceding_comments = ts_utils.get_preceding_doc_comments(class_node)
        docstring = ts_utils.extract_docstring_from_comment_nodes(preceding_comments, content_bytes)
        
        class_element = {
            "file_path": relative_file_path_str,
            "element_type": "class",
            "name": class_name,
            "qualified_name": _build_fqn_js(relative_file_path_str, class_name, project_root_name=project_name_for_fqn),
            "language": LANG_JS,
            "start_line": class_node.start_point[0] + 1,
            "end_line": class_node.end_point[0] + 1,
            "docstring": docstring,
            "source_code": ts_utils.get_node_text_from_bytes(class_node, content_bytes),
            "methods": [] # Could be populated by iterating class body for method_definitions if not making them top-level elements
        }
        elements.append(class_element)

    log.debug("extracted_javascript_elements_from_file", file=relative_file_path_str, count=len(elements))
    return elements