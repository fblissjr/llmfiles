# llmfiles/util.py
"""general utility functions for the llmfiles application."""

import structlog  # use structlog for consistency

log = structlog.get_logger(__name__)  # module-level logger
utf8_bom = b"\xef\xbb\xbf"  # utf-8 byte order mark sequence.


def strip_utf8_bom(data: bytes) -> bytes:
    """removes utf-8 bom from byte data if present."""
    if data.startswith(utf8_bom):
        log.debug("utf-8 bom stripped from data.")
        return data[len(utf8_bom) :]
    return data

def get_language_hint(extension: str | None) -> str:
    """
    provides a language hint for markdown code blocks based on file extension.
    returns lowercase extension if no specific hint, or empty string for no extension.
    """
    if not extension:
        return ""
    ext_low = extension.lower().strip(".")
    # comprehensive map of common extensions to language hints.
    # aims to provide good default hints for llm syntax highlighting.
    ext_map = {
        "py": "python",
        "pyw": "python",
        "js": "javascript",
        "jsx": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "java": "java",
        "c": "c",
        "h": "c",
        "cpp": "cpp",
        "cxx": "cpp",
        "hpp": "cpp",
        "hxx": "cpp",
        "cc": "cpp",
        "hh": "cpp",
        "cs": "csharp",
        "go": "go",
        "rb": "ruby",
        "rbw": "ruby",
        "php": "php",
        "phtml": "php",
        "swift": "swift",
        "kt": "kotlin",
        "kts": "kotlin",
        "rs": "rust",
        "scala": "scala",
        "sc": "scala",
        "sh": "bash",
        "bash": "bash",
        "zsh": "zsh",
        "fish": "fish",
        "ps1": "powershell",
        "psm1": "powershell",
        "md": "markdown",
        "markdown": "markdown",
        "mdx": "markdown",
        "json": "json",
        "jsonc": "json",
        "geojson": "json",
        "ipynb": "json",  # jupyter notebooks are json
        "yaml": "yaml",
        "yml": "yaml",
        "xml": "xml",
        "xsl": "xml",
        "xslt": "xml",
        "xsd": "xml",
        "plist": "xml",
        "svg": "xml",
        "html": "html",
        "htm": "html",
        "xhtml": "html",
        "css": "css",
        "scss": "scss",
        "sass": "sass",
        "less": "less",
        "sql": "sql",
        "ddl": "sql",
        "dml": "sql",
        "dockerfile": "dockerfile",
        "dockerignore": "dockerignore",
        "toml": "toml",
        "ini": "ini",
        "cfg": "ini",
        "conf": "ini",
        "properties": "ini",
        "env": "bash",  # .env files often resemble shell syntax
        "hbs": "handlebars",
        "mustache": "handlebars",
        "tf": "terraform",
        "tfvars": "terraform",
        "hcl": "terraform",
        "pl": "perl",
        "pm": "perl",
        "lua": "lua",
        "r": "r",
        "R": "r",
        "dart": "dart",
        "fs": "fsharp",
        "fsi": "fsharp",
        "fsx": "fsharp",
        "vb": "vbnet",
        "vbs": "vbscript",
        "tex": "latex",
        "sty": "latex",
        "erl": "erlang",
        "hrl": "erlang",
        "ex": "elixir",
        "exs": "elixir",
        "clj": "clojure",
        "cljs": "clojure",
        "cljc": "clojure",
        "edn": "clojure",
        "hs": "haskell",
        "lhs": "haskell",
        "svelte": "svelte",
        "vue": "vue",
        "diff": "diff",
        "patch": "diff",
        "groovy": "groovy",
        "gvy": "groovy",
        "gradle": "groovy",  # gradle build scripts
        "bat": "batch",
        "cmd": "batch",
        "gitignore": "gitignore",
        "http": "http",  # .http or .rest files
        "graphql": "graphql",
        "gql": "graphql",
        "proto": "protobuf",
        "sol": "solidity",
        "lock": "text",  # lock files vary (json, yaml, custom text) - text is a safe default
    }
    return ext_map.get(ext_low, ext_low)