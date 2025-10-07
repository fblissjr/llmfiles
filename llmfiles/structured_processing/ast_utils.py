# llmfiles/structured_processing/ast_utils.py
from tree_sitter import Parser, Language, Node
from typing import Dict, Any, Optional, List, Tuple
import textwrap
import structlog

log = structlog.get_logger(__name__)

LANG_CONFIG_TS: Dict[str, Dict[str, Any]] = {}
PARSERS_TS: Dict[str, Parser] = {}
QUERIES_COMPILED_TS: Dict[str, Dict[str, Any]] = {}

LANGUAGE_PROVIDER_MODULE_NAME = "tree_sitter_language_pack"

_get_language_from_provider: Optional[callable] = None
try:
    provider_module = __import__(LANGUAGE_PROVIDER_MODULE_NAME, fromlist=['get_language'])
    _get_language_from_provider = getattr(provider_module, 'get_language', None)
    if not _get_language_from_provider:
        log.error("get_language_function_not_found_in_provider", provider=LANGUAGE_PROVIDER_MODULE_NAME)
except ImportError:
    log.error("failed_to_import_tree_sitter_language_provider", module_name=LANGUAGE_PROVIDER_MODULE_NAME)

def load_language_configs_for_llmfiles():
    global LANG_CONFIG_TS
    if LANG_CONFIG_TS:
        return

    if not _get_language_from_provider:
        log.error("cannot_load_language_configs_provider_unavailable")
        return

    log.info("initializing_tree_sitter_language_configurations")

    try:
        py_lang_obj: Optional[Language] = _get_language_from_provider("python")
        if py_lang_obj:
            LANG_CONFIG_TS["python"] = {
                "ts_language_object": py_lang_obj,
                "queries": {
                    "functions": "(function_definition name: (identifier) @function.name) @function.definition",
                    "classes": "(class_definition name: (identifier) @class.name) @class.definition",
                    "imports": """
                        [
                          (import_statement name: (dotted_name) @import)
                          (from_import_statement module_name: (dotted_name) @import)
                        ]
                    """
                },
                "node_types": {
                     "function_definition": "function_definition",
                     "class_definition": "class_definition", "identifier": "identifier",
                     "block": "block", "string": "string", "expression_statement": "expression_statement",
                }
            }
    except Exception as e:
        log.warning("failed_to_load_python_language_config", error=str(e))

    try:
        js_lang_obj: Optional[Language] = _get_language_from_provider("javascript")
        if js_lang_obj:
            LANG_CONFIG_TS["javascript"] = {
                "ts_language_object": js_lang_obj,
                "queries": {
                    "functions": """
                        [
                          (function_declaration name: (identifier) @function.name) @function.definition
                          (arrow_function) @function.definition
                          (method_definition name: (property_identifier) @method.name) @method.definition
                        ]
                    """,
                    "classes": "(class_declaration name: (identifier) @class.name) @class.definition",
                },
                "node_types": {
                    "function_declaration": "function_declaration", "arrow_function": "arrow_function",
                    "method_definition": "method_definition", "class_declaration": "class_declaration",
                    "identifier": "identifier", "property_identifier": "property_identifier",
                    "statement_block": "statement_block", "comment": "comment", "variable_declarator": "variable_declarator",
                }
            }
    except Exception as e:
        log.warning("failed_to_load_javascript_language_config", error=str(e))

def _ensure_parser_initialized(lang_name: str) -> Optional[Parser]:
    if lang_name not in PARSERS_TS:
        if lang_name not in LANG_CONFIG_TS:
            return None
        try:
            parser = Parser()
            ts_lang_obj = LANG_CONFIG_TS[lang_name]["ts_language_object"]
            parser.language = ts_lang_obj
            PARSERS_TS[lang_name] = parser

            QUERIES_COMPILED_TS[lang_name] = {}
            for query_name, query_string in LANG_CONFIG_TS[lang_name].get("queries", {}).items():
                try:
                    QUERIES_COMPILED_TS[lang_name][query_name] = ts_lang_obj.query(query_string)
                except Exception as e:
                    log.warning("failed_to_compile_query", lang=lang_name, query=query_name, error=str(e))
        except Exception as e:
            log.error("parser_initialization_failed", lang=lang_name, error=str(e))
            return None
    return PARSERS_TS.get(lang_name)

def parse_code_to_ast(content_bytes: bytes, language_name: str) -> Optional[Node]:
    parser = _ensure_parser_initialized(language_name)
    if not parser: return None
    try:
        return parser.parse(content_bytes).root_node
    except Exception as e:
        log.error("code_parsing_failed", lang=language_name, error=str(e))
        return None

def get_node_text(node: Optional[Node], content_bytes: bytes) -> str:
    if node:
        return content_bytes[node.start_byte:node.end_byte].decode('utf-8', 'replace')
    return ""

def run_query(query_key: str, lang_name: str, node: Node) -> List[Tuple[Node, str]]:
    query_obj = QUERIES_COMPILED_TS.get(lang_name, {}).get(query_key)
    if not query_obj:
        return []
    try:
        captures_dict = query_obj.captures(node)
        result = []
        for capture_name, nodes in captures_dict.items():
            for n in nodes:
                result.append((n, capture_name))
        return result
    except Exception as e:
        log.error("tree_sitter_query_execution_failed", query=query_key, lang=lang_name, error=str(e))
        return []

def get_node_type(lang_name: str, type_key: str) -> Optional[str]:
    return LANG_CONFIG_TS.get(lang_name, {}).get("node_types", {}).get(type_key)

def is_node_type(node: Optional[Node], lang_name: str, type_key: str) -> bool:
    if not node: return False
    expected_type = get_node_type(lang_name, type_key)
    return expected_type is not None and node.type == expected_type

def find_child_by_field(node: Optional[Node], field_name: str) -> Optional[Node]:
    return node.child_by_field_name(field_name) if node else None

def get_docstring_from_js_comments(comment_nodes: List[Node], content_bytes: bytes) -> Optional[str]:
    doc_lines: List[str] = []
    for comment_node in comment_nodes:
        comment_text = get_node_text(comment_node, content_bytes)
        if comment_text.startswith("/**"):
            cleaned_block = comment_text[3:-2]
            for line in cleaned_block.splitlines():
                doc_lines.append(line.strip().lstrip("*").strip())
    return textwrap.dedent("\n".join(doc_lines)).strip() if doc_lines else None

def get_js_doc_comment_nodes(node: Node) -> List[Node]:
    doc_comment_nodes: List[Node] = []
    sibling = node.prev_sibling
    while sibling:
        if sibling.type == "comment":
            doc_comment_nodes.append(sibling)
        elif sibling.is_named:
            break
        sibling = sibling.prev_sibling
    return list(reversed(doc_comment_nodes))

def get_python_docstring(body_node: Optional[Node], content_bytes: bytes) -> Optional[str]:
    if not body_node or not is_node_type(body_node, "python", "block") or not body_node.named_children:
        return None

    first_statement = body_node.named_children[0]
    if is_node_type(first_statement, "python", "expression_statement"):
        string_node = first_statement.named_child(0)
        if string_node and is_node_type(string_node, "python", "string"):
            raw_docstring = get_node_text(string_node, content_bytes)

            if (raw_docstring.startswith('"""') and raw_docstring.endswith('"""')) or \
               (raw_docstring.startswith("'''") and raw_docstring.endswith("'''")):
                clean_docstring = raw_docstring[3:-3]
            elif (raw_docstring.startswith('"') and raw_docstring.endswith('"')) or \
                 (raw_docstring.startswith("'") and raw_docstring.endswith("'")):
                clean_docstring = raw_docstring[1:-1]
            else:
                clean_docstring = raw_docstring

            return textwrap.dedent(clean_docstring).strip()
    return None
