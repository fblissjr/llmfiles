# llmfiles/util.py
"""General utility functions for the llmfiles application."""

import logging

logger = logging.getLogger(__name__)
UTF8_BOM = b"\xef\xbb\xbf"  # UTF-8 Byte Order Mark sequence.


def strip_utf8_bom(data: bytes) -> bytes:
    """Removes UTF-8 BOM from byte data if present."""
    if data.startswith(UTF8_BOM):
        logger.debug("UTF-8 BOM stripped from data.")
        return data[len(UTF8_BOM):]
    return data

def get_language_hint(extension: str | None) -> str:
    """
    Provides a language hint for Markdown code blocks based on file extension.
    Returns lowercase extension if no specific hint is found, or empty string for no extension.
    """
    if not extension:
        return ""
    ext_low = extension.lower().strip(".")
    # Comprehensive map of common extensions to language hints.
    ext_map = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "java": "java",
        "c": "c",
        "cpp": "cpp",
        "cs": "csharp",
        "go": "go",
        "rb": "ruby",
        "php": "php",
        "swift": "swift",
        "kt": "kotlin",
        "rs": "rust",
        "scala": "scala",
        "sh": "bash",
        "ps1": "powershell",
        "md": "markdown",
        "json": "json",
        "yaml": "yaml",
        "yml": "yaml",
        "xml": "xml",
        "html": "html",
        "css": "css",
        "sql": "sql",
        "dockerfile": "dockerfile",
        "toml": "toml",
        "ini": "ini",
        "hbs": "handlebars",
        "tf": "terraform",
        "gitignore": "gitignore",
        # Add more mappings as needed
    }
    return ext_map.get(ext_low, ext_low)  # Default to the extension itself