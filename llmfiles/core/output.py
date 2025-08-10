import sys
from pathlib import Path
import structlog
from llmfiles.exceptions import OutputError

log = structlog.get_logger(__name__)

def write_to_stdout(text_content: str):
    # writes text to standard output.
    try:
        sys.stdout.write(text_content)
        sys.stdout.flush()
    except Exception as e:
        log.warning("stdout_write_failed_trying_binary_fallback", error=str(e))
        try:
            sys.stdout.buffer.write(text_content.encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        except Exception as inner_e:
            log.critical("stdout_binary_fallback_failed_critical_error", error=str(inner_e))

def write_to_file(output_file_path: Path, text_content: str):
    # writes text content to the specified file path.
    log.info("writing_output_to_file", path=str(output_file_path))
    try:
        output_file_path.write_text(text_content, encoding="utf-8")
    except Exception as e:
        raise OutputError(f"failed to write to file '{output_file_path}': {e}")
