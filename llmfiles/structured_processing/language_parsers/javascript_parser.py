from pathlib import Path
from typing import Dict, Any, List, Optional
import structlog

from llmfiles.structured_processing import ast_utils as ts

log = structlog.get_logger(__name__)
LANG = "javascript"

def _build_fqn(file_rel_path: str, el_name: Optional[str], class_name: Optional[str] = None) -> str:
    # builds a javascript-style fully qualified name.
    path_parts = list(Path(file_rel_path).parts)
    if path_parts and path_parts[-1].endswith((".js", ".jsx", ".ts", ".tsx", ".mjs")):
        path_parts[-1] = Path(path_parts[-1]).stem

    if path_parts and path_parts[0] in ("src", "lib", "app"):
        path_parts.pop(0)

    fqn_parts = path_parts
    if class_name: fqn_parts.append(class_name)
    if el_name: fqn_parts.append(el_name)

    return ".".join(filter(None, fqn_parts)) or "anonymous_element"

def _get_element_name(node: ts.Node, content_bytes: bytes) -> Optional[str]:
    # helper to get a name from various declaration types.
    name_node = ts.find_child_by_field(node, "name")
    if name_node:
        return ts.get_node_text(name_node, content_bytes)
    # handles `const myFunc = () => {}`
    if node.parent and ts.is_node_type(node.parent, LANG, "variable_declarator"):
        name_node = ts.find_child_by_field(node.parent, "name")
        return ts.get_node_text(name_node, content_bytes)
    return None

def extract_javascript_elements(file_path: Path, project_root: Path, content_bytes: bytes) -> List[Dict[str, Any]]:
    # parses a javascript file and extracts functions, classes, and methods.
    elements: List[Dict[str, Any]] = []
    rel_path = str(file_path.relative_to(project_root))
    ast = ts.parse_code_to_ast(content_bytes, LANG)
    if not ast:
        return elements

    processed_nodes = set()

    captures = ts.run_query("functions", LANG, ast) + ts.run_query("classes", LANG, ast)

    for node, capture_name in captures:
        if node.id in processed_nodes:
            continue
        processed_nodes.add(node.id)

        element_type = "function"
        if "class" in capture_name:
            element_type = "class"
        elif "method" in capture_name:
            element_type = "method"

        name = _get_element_name(node, content_bytes)

        parent_class_name = None
        if element_type == "method":
            # traverse up to find the parent class node.
            parent = node.parent
            while parent:
                if ts.is_node_type(parent, LANG, "class_declaration"):
                    parent_class_name = _get_element_name(parent, content_bytes)
                    break
                parent = parent.parent

        comment_nodes = ts.get_js_doc_comment_nodes(node)
        docstring = ts.get_docstring_from_js_comments(comment_nodes, content_bytes)

        elements.append({
            "file_path": rel_path,
            "element_type": element_type,
            "name": name or f"anonymous_{node.type}",
            "qualified_name": _build_fqn(rel_path, name, class_name=parent_class_name),
            "language": LANG,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "docstring": docstring,
            "source_code": ts.get_node_text(node, content_bytes),
        })

    log.debug("extracted_js_elements", file=rel_path, count=len(elements))
    return elements
