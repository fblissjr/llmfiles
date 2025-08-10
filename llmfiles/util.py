import structlog

log = structlog.get_logger(__name__)
utf8_bom = b"\xef\xbb\xbf"

def strip_utf8_bom(data: bytes) -> bytes:
    # removes the utf-8 byte order mark from byte data if present.
    if data.startswith(utf8_bom):
        return data[len(utf8_bom):]
    return data

def get_language_hint(extension: str | None) -> str:
    # provides a language hint for markdown code blocks based on file extension.
    if not extension:
        return ""
    ext = extension.lower().strip(".")
    ext_map = {
        "py": "python", "js": "javascript", "ts": "typescript", "java": "java",
        "c": "c", "h": "c", "cpp": "cpp", "hpp": "cpp", "cs": "csharp", "go": "go",
        "rb": "ruby", "php": "php", "swift": "swift", "kt": "kotlin", "rs": "rust",
        "scala": "scala", "sh": "bash", "md": "markdown", "json": "json",
        "yaml": "yaml", "yml": "yaml", "xml": "xml", "html": "html", "css": "css",
        "sql": "sql", "dockerfile": "dockerfile", "toml": "toml", "ini": "ini",
    }
    return ext_map.get(ext, ext)
