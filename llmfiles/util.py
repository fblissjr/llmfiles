# llmfiles/util.py
"""Utility functions."""
import logging

logger = logging.getLogger(__name__)
UTF8_BOM = b'\xef\xbb\xbf'

def strip_utf8_bom(data: bytes) -> bytes:
    """Removes UTF-8 BOM if present."""
    if data.startswith(UTF8_BOM):
        return data[len(UTF8_BOM):]
    return data

def get_language_hint(extension: str | None) -> str:
    """Provides a language hint for Markdown code blocks."""
    # Basic mapping, can be expanded
    ext_map = {
        "py": "python", "js": "javascript", "ts": "typescript",
        "java": "java", "c": "c", "cpp": "cpp", "cs": "csharp",
        "go": "go", "rb": "ruby", "php": "php", "swift": "swift",
        "kt": "kotlin", "rs": "rust", "scala": "scala",
        "sh": "bash", "ps1": "powershell", "md": "markdown",
        "json": "json", "yaml": "yaml", "yml": "yaml", "xml": "xml",
        "html": "html", "css": "css", "sql": "sql", "dockerfile": "dockerfile",
        "toml": "toml", "ini": "ini", "hbs": "handlebars", "hbr": "handlebars",
    }
    if extension:
        return ext_map.get(extension.lower(), extension.lower())
    return ""