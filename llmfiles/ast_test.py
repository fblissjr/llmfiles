# tests/ast_test.py (or llmfiles/ast_test.py if you run it from project root)
import sys
from pathlib import Path
import logging # For basic logging config for the test script

# Configure basic logging to see output from llmfiles modules
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)-7s] %(name)s: %(message)s')

# Correct path adjustment for running script from project root or tests directory
# If ast_test.py is in llmfiles/ (project root for this script's perspective)
script_dir = Path(__file__).resolve().parent
project_root = script_dir # If ast_test.py is in the project root (alongside the top 'llmfiles' package)
# If ast_test.py is in llmfiles/tests/ then project_root should be script_dir.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(project_root.parent) not in sys.path and (project_root / 'llmfiles').is_dir(): # If running from within 'llmfiles' package dir itself
     sys.path.insert(0, str(project_root.parent))


from llmfiles.structured_processing import ast_utils

print(f"Attempting to load language configurations...")
# This is the main function to call to load all languages defined within it.
ast_utils.load_language_configs_for_llmfiles() 

# Check if configurations were loaded by inspecting the global dict
if "python" in ast_utils.LANG_CONFIG_TS:
    print(f"Python config loaded. Language object: {ast_utils.LANG_CONFIG_TS['python']['ts_language_object']}")
else:
    print("Python FAILED to load from config.")

if "javascript" in ast_utils.LANG_CONFIG_TS:
    print(f"JavaScript config loaded. Language object: {ast_utils.LANG_CONFIG_TS['javascript']['ts_language_object']}")
else:
    print("JavaScript FAILED to load from config.")

print("\n--- Testing parser initialization and basic parsing ---")

# Test Python parsing (relies on _ensure_parser_initialized called by parse_code_to_ast)
print("\nTesting Python:")
py_code_bytes = b"def hello_py():\n  print('Hello from Python')\n\nclass MyPyClass:\n  pass"
py_ast_root = ast_utils.parse_code_to_ast(py_code_bytes, "python")
if py_ast_root:
    print(f"Successfully parsed Python code. Root node type: {py_ast_root.type}")
    s_expression = py_ast_root.sexp() if hasattr(py_ast_root, 'sexp') else str(py_ast_root)
    print(f"Python AST S-expression (excerpt): {s_expression[:200]}...")
else:
    print("FAILED to parse Python code snippet.")

# Test JavaScript parsing
print("\nTesting JavaScript:")
js_code_bytes = b"function helloJs() { console.log('Hello from JS'); }\nclass MyJsClass {}"
js_ast_root = ast_utils.parse_code_to_ast(js_code_bytes, "javascript")
if js_ast_root:
    print(f"Successfully parsed JavaScript code. Root node type: {js_ast_root.type}")
    s_expression_js = js_ast_root.sexp() if hasattr(js_ast_root, 'sexp') else str(js_ast_root)
    print(f"JavaScript AST S-expression (excerpt): {s_expression_js[:200]}...") 
else:
    print("FAILED to parse JavaScript code snippet.")

print("\n--- Test complete ---")