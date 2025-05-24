# llmfiles/core/processing.py
"""
Handles reading file content and transforming it into processable elements,
applying strategies like chunking or whole-file processing, and formatting.
"""
import structlog
from pathlib import Path
from typing import Optional, List, Dict, Any # Tuple not used

from llmfiles.config.settings import PromptConfig, ChunkStrategy, SortMethod 
from llmfiles.structured_processing import ast_utils
from llmfiles.structured_processing.language_parsers import python_parser, javascript_parser
from llmfiles.util import strip_utf8_bom, get_language_hint

log = structlog.get_logger(__name__)

# --- Content Formatting Helper (for individual elements/chunks) ---
def _format_element_output_content(
    raw_element_content: str, 
    config: PromptConfig,
    language_hint: str, 
    element_start_line_in_file: int = 1 
) -> str:
    """Applies line numbering and code block wrapping to an element's content string."""
    content_lines = raw_element_content.splitlines()
    start_line_offset = element_start_line_in_file -1 

    if config.line_numbers:
        max_line_num = len(content_lines) + start_line_offset
        max_line_num_width = len(str(max_line_num)) if max_line_num > 0 else 1
        
        formatted_lines = [
            f"{str(i + 1 + start_line_offset):>{max_line_num_width}} | {line}"
            for i, line in enumerate(content_lines)
        ]
        processed_content_str = "\n".join(formatted_lines)
    else:
        processed_content_str = raw_element_content
    
    if not config.no_codeblock:
        backtick_seq = "```"
        while backtick_seq in processed_content_str: backtick_seq += "`"
        final_formatted_content = f"{backtick_seq}{language_hint}\n{processed_content_str}\n{backtick_seq}"
    else:
        final_formatted_content = processed_content_str
        
    return final_formatted_content

# --- Main Function: process_file_content_to_elements ---
def process_file_content_to_elements(file_path: Path, config: PromptConfig) -> List[Dict[str, Any]]:
    log.debug("processing_file_to_elements_started", path=str(file_path), strategy=config.chunk_strategy.value)
    elements: List[Dict[str, Any]] = []
    
    try:
        content_bytes = file_path.read_bytes()
    except Exception as e:
        log.warning("file_read_error_in_processing", path=str(file_path), error=str(e))
        return elements

    base_text_content = strip_utf8_bom(content_bytes).decode("utf-8", errors="replace")

    if "\ufffd" in base_text_content:
        log.info("skipping_file_due_to_decode_errors_likely_binary", path=str(file_path))
        return elements
    if not base_text_content.strip():
        log.info("skipping_empty_or_whitespace_only_file_content", path=str(file_path))
        return elements

    file_rel_path_str = str(file_path.relative_to(config.base_dir) if config.base_dir and file_path.is_relative_to(config.base_dir) else file_path.name)
    file_mod_time: Optional[float] = None
    if config.sort_method in [SortMethod.DATE_ASC, SortMethod.DATE_DESC]:
        try: file_mod_time = file_path.stat().st_mtime
        except OSError as e: log.warning("failed_to_get_modification_time", path=str(file_path), error=str(e))
    
    file_lang_ext = file_path.suffix[1:].lower() if file_path.suffix else ""
    file_lang_hint_for_codeblocks = get_language_hint(file_lang_ext) 
    
    processed_by_chunker = False
    if config.chunk_strategy == ChunkStrategy.PYTHON_STRUCTURE and file_lang_ext == "py":
        if "python" in ast_utils.LANG_CONFIG_TS and ast_utils.PARSERS_TS.get("python"):
            log.debug("applying_python_structure_chunking_strategy", path=str(file_path))
            extracted_py_elements = python_parser.extract_python_elements(
                file_path, config.base_dir, content_bytes 
            )
            for py_el_data in extracted_py_elements:
                formatted_content = _format_element_output_content(
                    py_el_data["source_code"], config, "python", 
                    py_el_data["start_line"] 
                )
                elements.append({
                    "file_path": file_rel_path_str, "mod_time": file_mod_time,
                    "element_type": py_el_data["element_type"], "name": py_el_data.get("name"),
                    "qualified_name": py_el_data.get("qualified_name"), "language": "python",
                    "start_line": py_el_data["start_line"], "end_line": py_el_data["end_line"],
                    "raw_content": py_el_data["source_code"], 
                    "docstring": py_el_data.get("docstring"), 
                    "signature_details": py_el_data.get("signature_details"), 
                    "llm_formatted_content": formatted_content
                })
            processed_by_chunker = True
    elif config.chunk_strategy == ChunkStrategy.PYTHON_STRUCTURE and file_lang_ext in ("js", "jsx", "mjs", "ts", "tsx") : # Assuming PYTHON_STRUCTURE implies general structure parsing
        if "javascript" in ast_utils.LANG_CONFIG_TS and ast_utils.PARSERS_TS.get("javascript"):
            log.debug("applying_javascript_structure_chunking_strategy", path=str(file_path))
            extracted_js_elements = javascript_parser.extract_javascript_elements(
                file_path, config.base_dir, content_bytes
            )
            for js_el_data in extracted_js_elements:
                formatted_content = _format_element_output_content(
                    js_el_data["source_code"], config, "javascript", # Use "javascript" as hint
                    js_el_data["start_line"]
                )
                elements.append({
                    "file_path": file_rel_path_str, "mod_time": file_mod_time,
                    "element_type": js_el_data["element_type"], 
                    "name": js_el_data.get("name"),
                    "qualified_name": js_el_data.get("qualified_name"), 
                    "language": "javascript", # Store consistent language name
                    "start_line": js_el_data["start_line"], 
                    "end_line": js_el_data["end_line"],
                    "raw_content": js_el_data["source_code"],
                    "docstring": js_el_data.get("docstring"),
                    "signature_details": js_el_data.get("signature_details"),
                    "llm_formatted_content": formatted_content
                })
            processed_by_chunker = True
        else:
            log.warning("javascript_structure_chunking_skipped_parser_unavailable", file=str(file_path))

    if not processed_by_chunker: 
        log.debug("applying_default_file_as_single_element_strategy", path=str(file_path))
        formatted_content = _format_element_output_content(
            base_text_content, config, file_lang_hint_for_codeblocks, 1 
        )
        elements.append({
            "file_path": file_rel_path_str, "mod_time": file_mod_time,
            "element_type": "file", "language": file_lang_hint_for_codeblocks,
            "start_line": 1, "end_line": len(base_text_content.splitlines()),
            "raw_content": base_text_content, 
            "llm_formatted_content": formatted_content
        })
    
    if not elements: log.debug("no_elements_generated_for_file", path=str(file_path))
    return elements