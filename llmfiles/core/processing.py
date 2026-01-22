import ast
import structlog
from pathlib import Path
from typing import List, Dict, Any, Optional

from llmfiles.config.settings import PromptConfig, ChunkStrategy
from llmfiles.structured_processing.language_parsers import python_parser, javascript_parser
from llmfiles.structured_processing import ast_utils
from llmfiles.util import strip_utf8_bom, get_language_hint

log = structlog.get_logger(__name__)


def extract_module_description(content: str, language: str) -> Optional[str]:
    """Extract first line of module docstring as description.

    Currently supports Python only. Returns None for other languages
    or if no docstring is found.
    """
    if language != "python":
        return None

    try:
        tree = ast.parse(content)
        docstring = ast.get_docstring(tree)
        if docstring:
            # Return first non-empty line of the docstring
            first_line = docstring.strip().split('\n')[0].strip()
            return first_line if first_line else None
        return None
    except SyntaxError:
        return None


def _format_element_output_content(
    raw_element_content: str,
    config: PromptConfig,
    language_hint: str,
    element_start_line_in_file: int = 1
) -> str:
    # applies line numbering and code block wrapping to an element's content.
    content_lines = raw_element_content.splitlines()
    start_line_offset = element_start_line_in_file - 1

    if config.line_numbers:
        max_line_num = len(content_lines) + start_line_offset
        line_num_width = len(str(max_line_num))

        formatted_lines = [
            f"{str(i + 1 + start_line_offset):>{line_num_width}} | {line}"
            for i, line in enumerate(content_lines)
        ]
        processed_content_str = "\n".join(formatted_lines)
    else:
        processed_content_str = raw_element_content

    if not config.no_codeblock:
        # use a variable number of backticks to avoid issues with content.
        backticks = "```"
        while backticks in processed_content_str:
            backticks += "`"
        return f"{backticks}{language_hint}\n{processed_content_str}\n{backticks}"

    return processed_content_str

def process_file_content_to_elements(file_path: Path, config: PromptConfig) -> List[Dict[str, Any]]:
    # main function to process a single file into one or more content elements.
    log.debug("processing_file_to_elements", path=str(file_path), strategy=config.chunk_strategy.value)
    elements: List[Dict[str, Any]] = []

    try:
        content_bytes = file_path.read_bytes()
        file_size = len(content_bytes)
    except Exception as e:
        log.warning("file_read_error", path=str(file_path), error=str(e))
        return elements

    # Check file size limit if configured
    if config.max_file_size is not None and file_size > config.max_file_size:
        log.info("skipping_oversized_file", path=str(file_path), size_bytes=file_size, max_size=config.max_file_size)
        return elements

    base_text_content = strip_utf8_bom(content_bytes).decode("utf-8", errors="replace")

    if config.exclude_binary and "\ufffd" in base_text_content:
        log.info("skipping_binary_file", path=str(file_path), size_bytes=file_size)
        return elements
    if not base_text_content.strip():
        log.info("skipping_empty_file", path=str(file_path))
        return elements

    file_rel_path_str = str(file_path.relative_to(config.base_dir) if file_path.is_relative_to(config.base_dir) else file_path.name)
    file_lang_ext = file_path.suffix[1:].lower()
    file_lang_hint = get_language_hint(file_lang_ext)

    use_structure_chunking = (
        config.chunk_strategy == ChunkStrategy.STRUCTURE and
        file_lang_hint in ast_utils.LANG_CONFIG_TS
    )

    if use_structure_chunking:
        log.debug("applying_structure_chunking", path=str(file_path), language=file_lang_hint)
        parser_map = {
            "python": python_parser.extract_python_elements,
            "javascript": javascript_parser.extract_javascript_elements,
        }
        parser_func = parser_map.get(file_lang_hint)
        if parser_func:
            extracted_elements = parser_func(file_path, config.base_dir, content_bytes)
            # Extract module-level description for the first element
            module_description = extract_module_description(base_text_content, file_lang_hint)
            for i, element_data in enumerate(extracted_elements):
                formatted_content = _format_element_output_content(
                    element_data["source_code"], config, file_lang_hint,
                    element_data["start_line"]
                )
                element_data["llm_formatted_content"] = formatted_content
                element_data["file_size_bytes"] = file_size
                element_data["line_count"] = element_data["end_line"] - element_data["start_line"] + 1
                # Only first element gets module description
                element_data["description"] = module_description if i == 0 else None
                elements.append(element_data)

    if not elements:
        log.debug("falling_back_to_whole_file_chunking", path=str(file_path))
        formatted_content = _format_element_output_content(
            base_text_content, config, file_lang_hint, 1
        )
        line_count = len(base_text_content.splitlines())
        description = extract_module_description(base_text_content, file_lang_hint)
        elements.append({
            "file_path": file_rel_path_str,
            "element_type": "file",
            "language": file_lang_hint,
            "start_line": 1,
            "end_line": line_count,
            "line_count": line_count,
            "description": description,
            "raw_content": base_text_content,
            "llm_formatted_content": formatted_content,
            "name": file_path.name,
            "qualified_name": file_rel_path_str,
            "file_size_bytes": file_size
        })

    return elements
