from pathlib import Path
from typing import Dict, Any, List, Optional
import structlog

from llmfiles.structured_processing import ast_utils as ts

log = structlog.get_logger(__name__)
LANG = "python"

def _build_fqn(file_rel_path: str, el_name: str, class_name: Optional[str] = None) -> str:
    # builds a pythonic fully qualified name.
    path_parts = list(Path(file_rel_path).parts)
    if path_parts and path_parts[-1].endswith(".py"):
        path_parts[-1] = path_parts[-1][:-3]
    if path_parts and path_parts[-1] == "__init__":
        path_parts.pop()

    fqn_parts = path_parts
    if class_name: fqn_parts.append(class_name)
    fqn_parts.append(el_name)

    return ".".join(fqn_parts)

def extract_python_elements(file_path: Path, project_root: Path, content_bytes: bytes) -> List[Dict[str, Any]]:
    # parses a python file and extracts functions and classes.
    elements: List[Dict[str, Any]] = []
    rel_path = str(file_path.relative_to(project_root))
    ast = ts.parse_code_to_ast(content_bytes, LANG)
    if not ast:
        return elements

    func_captures = ts.run_query("functions", LANG, ast)
    class_captures = ts.run_query("classes", LANG, ast)

    for node, _ in func_captures:
        name_node = ts.find_child_by_field(node, "name")
        func_name = ts.get_node_text(name_node, content_bytes)
        if not func_name: continue

        body_node = ts.find_child_by_field(node, "body")
        docstring = ts.get_python_docstring(body_node, content_bytes)

        elements.append({
            "file_path": rel_path, "element_type": "function", "name": func_name,
            "qualified_name": _build_fqn(rel_path, func_name), "language": LANG,
            "start_line": node.start_point[0] + 1, "end_line": node.end_point[0] + 1,
            "docstring": docstring, "source_code": ts.get_node_text(node, content_bytes),
        })

    for node, _ in class_captures:
        name_node = ts.find_child_by_field(node, "name")
        class_name = ts.get_node_text(name_node, content_bytes)
        if not class_name: continue

        body_node = ts.find_child_by_field(node, "body")
        docstring = ts.get_python_docstring(body_node, content_bytes)

        class_element = {
            "file_path": rel_path, "element_type": "class", "name": class_name,
            "qualified_name": _build_fqn(rel_path, class_name), "language": LANG,
            "start_line": node.start_point[0] + 1, "end_line": node.end_point[0] + 1,
            "docstring": docstring, "source_code": ts.get_node_text(node, content_bytes),
        }
        elements.append(class_element)

        # extract methods as separate elements.
        if body_node:
            method_captures = ts.run_query("functions", LANG, body_node)
            for method_node, _ in method_captures:
                method_name_node = ts.find_child_by_field(method_node, "name")
                method_name = ts.get_node_text(method_name_node, content_bytes)
                if not method_name: continue

                method_body_node = ts.find_child_by_field(method_node, "body")
                method_docstring = ts.get_python_docstring(method_body_node, content_bytes)

                elements.append({
                    "file_path": rel_path, "element_type": "method", "name": method_name,
                    "qualified_name": _build_fqn(rel_path, method_name, class_name=class_name),
                    "language": LANG, "start_line": method_node.start_point[0] + 1,
                    "end_line": method_node.end_point[0] + 1, "docstring": method_docstring,
                    "source_code": ts.get_node_text(method_node, content_bytes),
                })

    log.debug("extracted_python_elements", file=rel_path, count=len(elements))
    return elements

def extract_python_imports(content_bytes: bytes) -> List[str]:
    # parses python code and extracts all unique import module names.
    imports = set()
    ast = ts.parse_code_to_ast(content_bytes, LANG)
    if not ast:
        return []

    import_captures = ts.run_query("imports", LANG, ast)
    for node, capture_name in import_captures:
        if capture_name == "import":
            import_text = ts.get_node_text(node, content_bytes)
            imports.add(import_text)

    log.debug("extracted_python_imports", count=len(imports))
    return sorted(list(imports))
