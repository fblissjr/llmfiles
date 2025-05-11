# llmfiles/output.py
"""Handles outputting the final prompt to stdout, file, or clipboard."""
import sys
import logging
from pathlib import Path
import pyperclip  # type: ignore # For clipboard, type ignore if using basic stub

from .exceptions import OutputError

logger = logging.getLogger(__name__)

def write_to_stdout(text: str):
    """Writes text to standard output, flushes."""
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception as e:  # Fallback for encoding issues on some terminals
        logger.warning(f"Stdout write failed: {e}. Attempting binary fallback.")
        try:
            sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        except Exception as ie:
            logger.critical(f"Stdout binary fallback failed: {ie}")

def write_to_file(path: Path, text: str):
    """Writes text to a file (UTF-8 encoded)."""
    logger.info(f"Writing output to file: {path}")
    try:
        path.write_text(text, encoding="utf-8")
        logger.info(f"Output successfully written to {path}")
    except Exception as e:
        raise OutputError(f"Failed to write to file '{path}': {e}")

def copy_to_clipboard(text: str) -> bool:
    """Copies text to system clipboard. Returns True on success, False on failure."""
    logger.info("Attempting to copy output to clipboard...")
    try:
        pyperclip.copy(text)
        logger.info("Successfully copied to clipboard.")
        print(
            "INFO: Prompt content copied to clipboard.", file=sys.stderr
        )  # User feedback
        return True
    except pyperclip.PyperclipException as e:  # Common errors (no clipboard tool)
        logger.warning(
            f"Clipboard copy failed (PyperclipException): {e}. Ensure clipboard utility (xclip/pbcopy) is installed."
        )
        print(
            "WARNING: Could not copy to clipboard. Ensure a clipboard utility is installed.",
            file=sys.stderr,
        )
        return False
    except Exception as e:  # Other unexpected errors
        logger.error(f"Unexpected clipboard copy error: {e}", exc_info=True)
        print(f"ERROR: Unexpected clipboard error: {e}", file=sys.stderr)
        return False