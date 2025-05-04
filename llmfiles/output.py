# llmfiles/output.py
"""Handles outputting the final prompt."""
import sys
import logging
from pathlib import Path
import pyperclip # type: ignore

from .exceptions import OutputError

logger = logging.getLogger(__name__)

def write_to_stdout(text: str):
    """Prints text to standard output."""
    try:
        print(text)
    except Exception as e:
        # Fallback for potential encoding issues on weird terminals
        try:
            print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
        except Exception as inner_e:
             logger.error(f"Failed to write to stdout, even with encoding fallback: {inner_e}")
             raise OutputError(f"Failed to write to stdout: {e}") from e


def write_to_file(path: Path, text: str):
    """Writes text to a file."""
    logger.info(f"Writing output to file: {path}")
    try:
        path.write_text(text, encoding="utf-8")
        logger.info("Output successfully written.")
    except Exception as e:
        raise OutputError(f"Failed to write to file {path}: {e}")


def copy_to_clipboard(text: str):
    """Copies text to the system clipboard."""
    logger.info("Attempting to copy output to clipboard...")
    try:
        pyperclip.copy(text)
        logger.info("Successfully copied to clipboard.")
        print("INFO: Prompt copied to clipboard.", file=sys.stderr)
        # Note: No reliable way to check *if* it actually worked beyond no exception
    except pyperclip.PyperclipException as e:
        # Common if no clipboard backend is found (e.g., headless server)
        logger.warning(f"Could not copy to clipboard: {e}. Displaying output.")
        print("\n--- CLIPBOARD COPY FAILED, OUTPUT BELOW ---\n", file=sys.stderr)
        write_to_stdout(text) # Display if copy failed
        # Don't raise an error, just warn and proceed
    except Exception as e:
         logger.error(f"An unexpected error occurred during clipboard copy: {e}")
         print("\n--- CLIPBOARD COPY FAILED (Unexpected Error), OUTPUT BELOW ---\n", file=sys.stderr)
         write_to_stdout(text) # Display if copy failed