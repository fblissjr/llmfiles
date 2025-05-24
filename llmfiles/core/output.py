# llmfiles/output.py
"""handles outputting the final prompt to stdout, file, or clipboard."""
import sys
from pathlib import Path
import pyperclip  # type: ignore # for clipboard operations
import structlog  # for structured logging
from llmfiles.exceptions import OutputError

log = structlog.get_logger(__name__)  # module-level logger


def write_to_stdout(text_content: str):
    """writes text to standard output and flushes to ensure visibility."""
    try:
        # use sys.stdout.write for direct control, avoiding extra newlines from print().
        sys.stdout.write(text_content)
        sys.stdout.flush()  # important if output is piped or for immediate display.
    except Exception as e:
        log.warning("stdout_write_failed_trying_binary_fallback", error=str(e))
        try:  # fallback for potential encoding issues on unusual terminals.
            sys.stdout.buffer.write(text_content.encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        except Exception as inner_e:
            log.critical(
                "stdout_binary_fallback_failed_critical_error",
                error=str(inner_e),
                exc_info=True,
            )
            # at this point, stdout is likely unusable.


def write_to_file(output_file_path: Path, text_content: str):
    """writes text content to the specified file path using utf-8 encoding."""
    log.info("writing_output_to_file", path=str(output_file_path))
    try:
        output_file_path.write_text(text_content, encoding="utf-8")
        log.debug("output_successfully_written_to_file", path=str(output_file_path))
    except Exception as e:  # handles various file i/o errors.
        log.error(
            "failed_to_write_output_file",
            path=str(output_file_path),
            error=str(e),
            exc_info=True,
        )
        raise OutputError(f"failed to write to file '{output_file_path}': {e}") from e

def copy_to_clipboard(text_content: str) -> bool:
    """
    copies text content to the system clipboard using pyperclip.
    prints user-facing messages about success/failure to stderr.
    returns true if successful, false otherwise.
    """
    log.info("attempting_to_copy_output_to_clipboard")
    try:
        pyperclip.copy(text_content)
        log.info("successfully_copied_to_clipboard_via_pyperclip")
        # user feedback should go to stderr, as stdout might be piped for the main output.
        print("info: prompt content copied to clipboard.", file=sys.stderr)
        return True
    except (
        pyperclip.PyperclipException
    ) as e:  # common pyperclip errors (e.g., no clipboard tool found).
        log.warning(
            "clipboard_copy_failed_pyperclip_exception",
            error=str(e),
            note="ensure clipboard utility (xclip/pbcopy) is installed and accessible.",
        )
        print(
            "warning: could not copy to clipboard. pyperclip library failed.\n"
            "ensure a clipboard mechanism (e.g., xclip, xsel on linux; pbcopy on macos) is installed.",
            file=sys.stderr,
        )
        return False
    except Exception as e:  # other unexpected errors during clipboard operation.
        log.error("unexpected_clipboard_copy_error", error=str(e), exc_info=True)
        print(
            f"error: an unexpected error occurred while trying to copy to clipboard: {e}",
            file=sys.stderr,
        )
        return False