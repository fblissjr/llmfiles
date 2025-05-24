# llmfiles/structured_processing/chunking_strategies.py
"""
Defines strategies for breaking down file content into meaningful elements (chunks).
Includes a base chunker, a default whole-file chunker, and structure-aware chunkers (e.g., for Tree-sitter).
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Any, Optional
import structlog

from llmfiles.config.settings import ChunkStrategy, PromptConfig # For config access
from llmfiles.util import strip_utf8_bom # For file processing
from llmfiles.core.processing import _format_element_output_content # For formatting chunks

# Import specific parsers conditionally or through a dispatcher
from llmfiles.structured_processing import ast_utils
from llmfiles.structured_processing.language_parsers import python_parser

log = structlog.get_logger(__name__)

class BaseChunker(ABC):
    """Abstract base class for content chunking strategies."""
    def __init__(self, config: PromptConfig):
        self.config = config

    @abstractmethod
    def chunk_file(self, file_path: Path, content_bytes: bytes, file_rel_path_str: str,
                   file_mod_time: Optional[float], file_lang_hint: str) -> List[Dict[str, Any]]:
        """
        Processes a file and returns a list of content element dictionaries.
        Each element should contain at least 'raw_content', 'llm_formatted_content',
        and other metadata like 'element_type', 'language', 'file_path', etc.
        """
        pass

class FileAsSingleChunker(BaseChunker):
    """Treats each entire file as a single content element."""
    def chunk_file(self, file_path: Path, content_bytes: bytes, file_rel_path_str: str,
                   file_mod_time: Optional[float], file_lang_hint: str) -> List[Dict[str, Any]]:
        
        elements: List[Dict[str, Any]] = []
        base_text_content = strip_utf8_bom(content_bytes).decode("utf-8", errors="replace")

        # Note: YAML specific truncation was removed from the main processing.py.
        # If needed for specific file types before chunking, it would go here or in a pre-step.
        
        if "\ufffd" in base_text_content: # Unicode replacement character suggests binary
            log.info("file_as_single_chunk_skipped_decode_errors", path=str(file_path))
            return elements
        if not base_text_content.strip():
            log.info("file_as_single_chunk_skipped_empty_content", path=str(file_path))
            return elements

        llm_formatted_content = _format_element_output_content(
            base_text_content, self.config, file_lang_hint, 1 # Whole file starts at line 1
        )
        elements.append({
            "file_path": file_rel_path_str, "mod_time": file_mod_time,
            "element_type": "file", "language": file_lang_hint,
            "start_line": 1, "end_line": len(base_text_content.splitlines()),
            "raw_content": base_text_content, 
            "llm_formatted_content": llm_formatted_content,
            "name": file_path.name, # Add file name as element name for consistency
            "qualified_name": file_rel_path_str # Use relative path as FQN for whole file element
        })
        return elements

class TreeSitterStructureChunker(BaseChunker):
    """Uses Tree-sitter to chunk files based on language-specific structures (functions, classes)."""
    def chunk_file(self, file_path: Path, content_bytes: bytes, file_rel_path_str: str,
                   file_mod_time: Optional[float], file_lang_hint: str) -> List[Dict[str, Any]]:
        elements: List[Dict[str, Any]] = []
        
        # Determine parser language from file_lang_hint or extension
        # This assumes file_lang_hint is a name recognized by ast_utils (e.g., "python", "javascript")
        parser_lang_name = file_lang_hint # Simplification: assume hint is the parser lang name
        
        if not parser_lang_name:
            log.debug("tree_sitter_chunking_skipped_no_language_hint", file=file_rel_path_str)
            return FileAsSingleChunker(self.config).chunk_file(file_path, content_bytes, file_rel_path_str, file_mod_time, file_lang_hint)

        # Check if parser for this language is available and initialized
        if parser_lang_name not in ast_utils.LANG_CONFIG_TS or not ast_utils.PARSERS_TS.get(parser_lang_name):
            log.warning("tree_sitter_chunking_skipped_parser_unavailable", language=parser_lang_name, file=file_rel_path_str)
            # Fallback to whole file chunking if specific parser isn't ready
            return FileAsSingleChunker(self.config).chunk_file(file_path, content_bytes, file_rel_path_str, file_mod_time, file_lang_hint)

        log.debug("applying_tree_sitter_structure_chunking", language=parser_lang_name, path=str(file_path))

        extracted_elements_data: List[Dict[str, Any]] = []
        if parser_lang_name == "python":
            extracted_elements_data = python_parser.extract_python_elements(
                file_path, self.config.base_dir, content_bytes 
            )
        # elif parser_lang_name == "javascript":
            # extracted_elements_data = javascript_parser.extract_javascript_elements(...)
        # Add other languages here
        else:
            log.warning("tree_sitter_chunking_unsupported_language_fallback_to_file", language=parser_lang_name, file=str(file_path))
            return FileAsSingleChunker(self.config).chunk_file(file_path, content_bytes, file_rel_path_str, file_mod_time, file_lang_hint)

        for el_data in extracted_elements_data:
            # el_data should conform to: { "element_type": "function", "name": ..., "qualified_name": ..., 
            # "source_code": ..., "docstring": ..., "start_line": ..., "end_line": ..., "signature_details": ... (optional) }
            
            formatted_content = _format_element_output_content(
                el_data["source_code"], self.config, parser_lang_name, # Use parser_lang_name as hint
                el_data["start_line"] 
            )
            elements.append({
                "file_path": file_rel_path_str, "mod_time": file_mod_time,
                "element_type": el_data["element_type"], 
                "name": el_data.get("name"),
                "qualified_name": el_data.get("qualified_name"), 
                "language": parser_lang_name, # The language parsed
                "start_line": el_data["start_line"], 
                "end_line": el_data["end_line"],
                "raw_content": el_data["source_code"], 
                "docstring": el_data.get("docstring"), 
                "signature_details": el_data.get("signature_details"), 
                "llm_formatted_content": formatted_content
            })
        
        # If after tree-sitter processing no elements were extracted (e.g. empty file, or no recognized structures)
        # consider falling back to whole file chunker or returning empty. For now, empty if no structural elements.
        if not elements and content_bytes.strip(): # If content exists but no chunks found
             log.debug("no_structural_elements_found_by_tree_sitter", file=file_rel_path_str, language=parser_lang_name)
             # Optionally, could fall back to FileAsSingleChunker here if desired
             # return FileAsSingleChunker(self.config).chunk_file(file_path, content_bytes, file_rel_path_str, file_mod_time, file_lang_hint)


        return elements

def get_chunker(config: PromptConfig) -> BaseChunker:
    """Factory function to get the appropriate chunker based on config."""
    if config.chunk_strategy == ChunkStrategy.PYTHON_STRUCTURE: # And other languages later
        # For PYTHON_STRUCTURE, TreeSitterStructureChunker will internally handle if it's a python file.
        return TreeSitterStructureChunker(config)
    # elif config.chunk_strategy == ChunkStrategy.SEMANTIC_TEXT:
        # return SemanticTextChunker(config) 
    return FileAsSingleChunker(config) # Default