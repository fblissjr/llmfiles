# llmfiles/structured_processing/ast_utils.py
"""
Tree-sitter setup, parser management, and generic AST helper functions.
Uses an installed tree-sitter language provider package.
"""
import sys
from tree_sitter import Parser, Language, Node 
from typing import Dict, Any, Optional, List, Tuple
import textwrap 
import structlog

log = structlog.get_logger(__name__)

# Globals to store loaded language configs, parsers, and compiled queries
LANG_CONFIG_TS: Dict[str, Dict[str, Any]] = {} 
PARSERS_TS: Dict[str, Parser] = {}
QUERIES_COMPILED_TS: Dict[str, Dict[str, Any]] = {}

# --- Language Provider Setup ---
# This is the Python package name that provides the tree-sitter language grammars.
# Common options: "tree_sitter_languages" (for py-tree-sitter-languages)
#                 "tree_sitter_language_pack" (if Goldziher's pack installs under this name and has get_language)
LANGUAGE_PROVIDER_MODULE_NAME = "tree_sitter_language_pack" 

_get_language_function_from_provider: Optional[callable] = None
try:
    # Dynamically import the provider package and get its 'get_language' function
    provider_module = __import__(LANGUAGE_PROVIDER_MODULE_NAME, fromlist=['get_language'])
    if hasattr(provider_module, 'get_language') and callable(provider_module.get_language):
        _get_language_function_from_provider = provider_module.get_language
        log.debug("tree_sitter_get_language_function_successfully_imported", 
                  provider=LANGUAGE_PROVIDER_MODULE_NAME)
    else:
        log.error("get_language_function_not_found_or_not_callable_in_provider", 
                  provider=LANGUAGE_PROVIDER_MODULE_NAME)
except ImportError:
    log.error("failed_to_import_tree_sitter_language_provider_module", 
              module_name=LANGUAGE_PROVIDER_MODULE_NAME,
              hint=f"Ensure '{LANGUAGE_PROVIDER_MODULE_NAME}' is installed and accessible.")
except Exception as e: # Catch any other error during import/access
    log.error("unexpected_error_importing_language_provider", 
              module_name=LANGUAGE_PROVIDER_MODULE_NAME, error=str(e), exc_info=True)

def load_language_configs_for_llmfiles():
    """
    Loads tree-sitter language configurations (Language objects, queries, node type mappings)
    for Python and JavaScript using the configured language provider.
    This function should be called once at application startup.
    """
    global LANG_CONFIG_TS # Allow modification of the global
    if LANG_CONFIG_TS: # Avoid re-initializing if already done
        log.debug("language_configs_already_loaded_skipping_reinit")
        return

    if not _get_language_function_from_provider:
        log.error("cannot_load_language_configs_as_provider_function_is_unavailable")
        return

    log.info("initializing_tree_sitter_language_configurations_via_provider")

    # --- Python Configuration ---
    try:
        py_lang_obj: Optional[Language] = _get_language_function_from_provider("python")
        if py_lang_obj and isinstance(py_lang_obj, Language):
            LANG_CONFIG_TS["python"] = {
                "ts_language_object": py_lang_obj,
                "queries": { 
                    "functions": "(function_definition name: (identifier) @function.name) @function.definition",
                    "classes": "(class_definition name: (identifier) @class.name) @class.definition",
                    "docstring_python": "(function_definition body: (block (expression_statement (string) @docstring)))" # Python specific
                },
                "node_types": { # Mapping internal llmfiles keys to actual tree-sitter node type strings
                     "function_definition": "function_definition", 
                     "class_definition": "class_definition",
                     "identifier": "identifier", 
                     "block": "block", # Body of functions/classes
                     "string": "string", 
                     "expression_statement": "expression_statement",
                     "comment": "comment", # Generic comment
                }
            }
            log.debug("python_tree_sitter_config_loaded_successfully")
        else:
            log.warning("python_language_object_not_returned_or_invalid_from_provider", received_obj=py_lang_obj)
    except Exception as e:
        log.warning("failed_to_load_python_language_config_via_provider", error=str(e), exc_info=True)
    
    # --- JavaScript Configuration ---
    try:
        js_lang_obj: Optional[Language] = _get_language_function_from_provider("javascript")
        if js_lang_obj and isinstance(js_lang_obj, Language):
            LANG_CONFIG_TS["javascript"] = {
                "ts_language_object": js_lang_obj,
                "queries": {
                "functions": """
                    [
                        (function_declaration name: (identifier) @function.name) @function.definition
                        (arrow_function) @function.definition_anon_arrow_or_expr
                        (method_definition name: (property_identifier) @method.name) @method.definition
                        
                        ; For named function expressions: const foo = function bar() { ... }
                        (function_expression name: (identifier) @function.name) @function.definition_named_expr

                        ; For anonymous function expressions: const foo = function() { ... }
                        ; This captures the (function) node. The name 'foo' is on the parent variable_declarator.
                        (function_expression) @function.definition_anon_expr
                    ]
                """,
                    "classes": """
                        [
                            (class_declaration name: (identifier) @class.name) @class.definition
                            (class name: (identifier) @class.name) @class.definition_expr
                        ]
                    """,
                    "comment": "(comment) @comment" # For JSDoc style comments
                },
                "node_types": {
                    "function_declaration": "function_declaration", 
                    "arrow_function": "arrow_function",
                    "function_expression": "function_expression",
                    "class_declaration": "class_declaration", "class_expression": "class",
                    "identifier": "identifier", "property_identifier": "property_identifier",
                    "statement_block": "statement_block", "string": "string", "comment": "comment",
                    "lexical_declaration": "lexical_declaration", 
                    "variable_declarator": "variable_declarator" 
                }
            }
            log.debug("javascript_tree_sitter_config_loaded_successfully")
        else:
            log.warning("javascript_language_object_not_returned_or_invalid_from_provider", received_obj=js_lang_obj)
    except Exception as e:
        log.warning("failed_to_load_javascript_language_config_via_provider", error=str(e), exc_info=True)
    
    if not LANG_CONFIG_TS:
        log.error("no_tree_sitter_language_configs_were_successfully_loaded",
                  detail="AST-based chunking and analysis will be unavailable for all languages.")

def _ensure_parser_initialized(lang_name: str) -> Optional[Parser]:
    """Initializes and returns a parser for the given language if not already done."""
    global PARSERS_TS, QUERIES_COMPILED_TS # Allow modification of globals
    if lang_name not in PARSERS_TS:
        if lang_name not in LANG_CONFIG_TS or "ts_language_object" not in LANG_CONFIG_TS[lang_name]:
            log.debug("language_config_or_object_not_found_for_parser_init", language=lang_name)
            return None
        try:
            parser = Parser()
            ts_lang_obj = LANG_CONFIG_TS[lang_name]["ts_language_object"]
            if not isinstance(ts_lang_obj, Language):
                 log.error("invalid_language_object_in_config_cannot_set_parser", language=lang_name, type_found=type(ts_lang_obj))
                 return None
            parser.language = ts_lang_obj
            PARSERS_TS[lang_name] = parser
            
            # Compile queries for this language
            QUERIES_COMPILED_TS[lang_name] = {} # Initialize specific lang query cache
            lang_specific_queries = LANG_CONFIG_TS[lang_name].get("queries", {})
            for query_name, query_string in lang_specific_queries.items():
                try:
                    compiled_query = ts_lang_obj.query(query_string)
                    QUERIES_COMPILED_TS[lang_name][query_name] = compiled_query
                except Exception as e_query: # tree_sitter.api.TreeSitterError, or general
                    log.warning("failed_to_compile_tree_sitter_query", language=lang_name, query_name=query_name, error=str(e_query))
            log.debug("parser_and_queries_initialized_for_language", language=lang_name)
        except Exception as e_parser: # Errors from parser.set_language or other setup
            log.error("tree_sitter_parser_initialization_failed", language=lang_name, error=str(e_parser), exc_info=True)
            return None
    return PARSERS_TS.get(lang_name)

def parse_code_to_ast(content_bytes: bytes, language_name: str) -> Optional[Node]:
    """Parses code bytes into a tree-sitter AST root node for the specified language."""
    parser = _ensure_parser_initialized(language_name)
    if not parser:
        log.warning("parser_unavailable_for_ast_parsing", language=language_name)
        return None
    try:
        tree = parser.parse(content_bytes)
        return tree.root_node
    except Exception as e:
        log.error("code_parsing_failed_with_tree_sitter", language=language_name, error=str(e), exc_info=True)
        return None

def get_node_text_from_bytes(node: Optional[Node], content_bytes: bytes) -> str:
    """Safely extracts UTF-8 text content of a tree-sitter node from original byte content."""
    if node and content_bytes is not None:
        if 0 <= node.start_byte < node.end_byte <= len(content_bytes):
            try:
                return content_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace')
            except IndexError: 
                log.warning("text_extraction_index_out_of_bounds", node_type=node.type, 
                            start=node.start_byte, end=node.end_byte, content_len=len(content_bytes))
                return "<text_extraction_error:index_error>"
        else: 
            # Typically for zero-width nodes or if start_byte == end_byte
            # log.debug("invalid_or_empty_node_byte_range_for_text", node_type=node.type,
            #            start=node.start_byte, end=node.end_byte)
            return "" 
    return "" # Default for None node or content_bytes

def run_ts_query(query_key: str, language_name: str, ast_node: Node) -> List[Tuple[Node, str]]:
    """Runs a pre-compiled tree-sitter query (from LANG_CONFIG_TS) against an AST node."""
    if language_name not in QUERIES_COMPILED_TS or query_key not in QUERIES_COMPILED_TS[language_name]:
        # This implies query wasn't compiled during _ensure_parser_initialized, or key is wrong
        # log.debug("query_not_found_or_not_compiled_for_language", query_key=query_key, language=language_name)
        return []
        
    query_obj = QUERIES_COMPILED_TS[language_name].get(query_key)
    if query_obj and ast_node:
        try: 
            return query_obj.captures(ast_node)
        except Exception as e: 
            log.error("tree_sitter_query_execution_runtime_error", query=query_key, lang=language_name, 
                      node_type=ast_node.type, error=str(e), exc_info=True)
    return []

def get_specific_node_type_name(language_name: str, internal_type_key: str) -> Optional[str]:
    """Retrieves the actual tree-sitter node type string for an internal key (e.g., 'function_definition')."""
    return LANG_CONFIG_TS.get(language_name, {}).get("node_types", {}).get(internal_type_key)

def is_node_of_type(node: Optional[Node], language_name: str, internal_type_key: str) -> bool:
    """Checks if a node matches a configured internal type key for the given language."""
    if not node: return False
    expected_ts_node_type = get_specific_node_type_name(language_name, internal_type_key)
    return expected_ts_node_type is not None and node.type == expected_ts_node_type

def find_named_child_by_field(node: Optional[Node], field_name: str) -> Optional[Node]:
    """Safely gets a child node by its (grammar-defined) field name."""
    if not node: return None
    return node.child_by_field_name(field_name)

def extract_docstring_from_jslike_comments(comment_nodes: List[Node], content_bytes: bytes) -> Optional[str]:
    """
    Extracts and cleans docstrings from a list of comment nodes, typical for JSDoc (/** ... */).
    """
    doc_lines: List[str] = []
    for comment_node in comment_nodes: # Assumes comment_nodes are ordered correctly
        comment_text = get_node_text_from_bytes(comment_node, content_bytes)
        if not comment_text: continue

        # Standard JSDoc / Multi-line block comment style for JS/TS
        if comment_node.type == "comment" and comment_text.startswith("/**") and comment_text.endswith("*/"):
            # Remove /** and */, then split into lines
            cleaned_block_content = comment_text[3:-2]
            current_block_lines = [
                line.strip().lstrip("*").strip() # Remove leading * and surrounding spaces from each line
                for line in cleaned_block_content.splitlines()
            ]
            # Add lines, preserving blank lines if they are between non-blank lines (maintains formatting)
            doc_lines.extend(line for line in current_block_lines if line or (doc_lines and doc_lines[-1]))
        # Could add handling for other comment styles if needed (e.g., consecutive `///`)
    
    return textwrap.dedent("\n".join(doc_lines)).strip() if doc_lines else None

def get_jslike_doc_comment_nodes(node: Node) -> List[Node]:
    """
    Collects 'comment' type sibling nodes that immediately precede the given AST node.
    Suitable for languages where docstrings are block comments before declarations (JS/TS).
    """
    doc_comment_nodes: List[Node] = []
    current_sibling = node.prev_sibling
    while current_sibling:
        # In many JS/TS grammars, comments are just 'comment'. Whitespace might be anonymous.
        if current_sibling.type == "comment":
            doc_comment_nodes.append(current_sibling)
        # Stop if we hit a non-comment, non-whitespace significant node
        elif current_sibling.is_named and current_sibling.type not in ("comment"): 
            break 
        elif not current_sibling.is_named and current_sibling.text.strip(): # Non-named but has text (not just whitespace)
            break
        current_sibling = current_sibling.prev_sibling
    return list(reversed(doc_comment_nodes)) # Comments are found bottom-up, so reverse

def extract_python_docstring(body_node: Optional[Node], content_bytes: bytes) -> Optional[str]:
    """Extracts and dedents a Python docstring (string literal as first statement in a block)."""
    if not body_node or not is_node_of_type(body_node, "python", "block") or not body_node.named_children:
        return None
    
    first_statement_node = body_node.named_children[0]
    if is_node_of_type(first_statement_node, "python", "expression_statement"):
        # The expression_statement's child is the string node itself
        string_literal_node = first_statement_node.named_child(0) 
        if string_literal_node and is_node_of_type(string_literal_node, "python", "string"):
            # get_node_text_from_bytes on the 'string' node gets the full string literal, including quotes.
            raw_docstring_with_quotes = get_node_text_from_bytes(string_literal_node, content_bytes)
            
            # Heuristic to remove quotes and prefixes like r"", u"", f""
            # This can be complex due to various string syntaxes (f-strings, raw, byte, concatenated)
            # For simplicity, focusing on common triple and single quotes.
            # A more robust solution might involve inspecting children of the 'string' node for 'string_content'.
            
            stripped_doc = raw_docstring_with_quotes
            # Try to remove common quotes
            if (stripped_doc.startswith('"""') and stripped_doc.endswith('"""')) or \
               (stripped_doc.startswith("'''") and stripped_doc.endswith("'''")):
                if len(stripped_doc) >= 6: stripped_doc = stripped_doc[3:-3]
            elif (stripped_doc.startswith('"') and stripped_doc.endswith('"')) or \
                 (stripped_doc.startswith("'") and stripped_doc.endswith("'")):
                if len(stripped_doc) >= 2: stripped_doc = stripped_doc[1:-1]
            
            # Further refine if prefixes like r, u, f were part of get_node_text output (unlikely for just string content)
            # This part of llmos-cli was very aggressive; tree-sitter usually gives clean content for 'string_content' children.
            # The get_node_text_from_bytes on the 'string' node might already be fairly clean.

            return textwrap.dedent(stripped_doc).strip() if stripped_doc.strip() else None
    return None