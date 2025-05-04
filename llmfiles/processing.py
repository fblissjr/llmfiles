# llmfiles/processing.py
"""File content processing functions."""
import logging
from pathlib import Path
from typing import Optional, Tuple

# Make sure SortMethod is imported
from .config import PromptConfig, SortMethod
from .util import strip_utf8_bom, get_language_hint
from .exceptions import ProcessingError

logger = logging.getLogger(__name__)

def process_file_content(path: Path, config: PromptConfig) -> Optional[Tuple[str, str, Optional[float]]]:
    """
    Reads, processes, and formats file content.
    Returns (processed_content, raw_content, mod_time) or None if skipped.
    mod_time is included only if date sorting is needed.
    """
    logger.debug(f"Processing file: {path}")
    try:
        raw_bytes = path.read_bytes()
        clean_bytes = strip_utf8_bom(raw_bytes)
        # Use errors='replace' during decode, but check for it later
        raw_content = clean_bytes.decode('utf-8', errors='replace')

        # Skip if decoding introduced replacement characters (likely binary)
        # The replacement character is U+FFFD
        if '\ufffd' in raw_content:
            logger.warning(f"Skipping file with decoding errors (likely binary): {path}")
            return None

        # Skip empty files (check after potential BOM strip)
        if not raw_content.strip():
             logger.debug(f"Skipping empty file: {path}")
             return None

        processed_lines = []
        lines_in_file = raw_content.splitlines()
        max_line_num_width = len(str(len(lines_in_file))) if config.line_numbers else 0

        for i, line in enumerate(lines_in_file):
            if config.line_numbers:
                processed_lines.append(f"{str(i+1):<{max_line_num_width}} | {line}")
            else:
                processed_lines.append(line)

        processed_content = "\n".join(processed_lines)

        # Wrap in code block if needed
        if not config.no_codeblock:
            lang_hint = get_language_hint(path.suffix[1:]) # Remove leading dot
            # Ensure triple backticks used for wrapping are more than any present in the content
            # While less common in code, it's good practice
            backticks = "```"
            while backticks in processed_content:
                backticks += "`"
            processed_content = f"{backticks}{lang_hint}\n{processed_content}\n{backticks}"

        mod_time = None
        # --- CORRECTED LINE ---
        if config.sort_method in [SortMethod.DATE_ASC, SortMethod.DATE_DESC]:
             try:
                 mod_time = path.stat().st_mtime
             except OSError as e:
                 logger.warning(f"Could not get modification time for {path}: {e}")

        return processed_content, raw_content, mod_time # Return raw_content too

    except FileNotFoundError:
        # This might happen in race conditions, but discover_paths should usually prevent it
        logger.error(f"File not found during processing: {path}")
        return None
    except PermissionError:
        logger.error(f"Permission denied for file: {path}")
        return None
    except MemoryError:
         # Important for very large files
         logger.error(f"Memory error reading large file: {path}")
         return None
    except Exception as e:
        # Catch unexpected errors during processing
        logger.error(f"Error processing file {path}: {e}")
        # Depending on policy, you might want to re-raise or just skip the file
        # raise ProcessingError(f"Failed to process {path}: {e}") from e
        return None