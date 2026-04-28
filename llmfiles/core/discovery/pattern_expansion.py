from pathlib import Path
from typing import List

_GLOB_CHARS = frozenset("*?[")


def _is_glob(piece: str) -> bool:
    return any(ch in _GLOB_CHARS for ch in piece)


def _expand_one(piece: str, base_dir: Path) -> str:
    piece = piece.strip()
    if not piece:
        return ""

    if _is_glob(piece):
        return piece

    if piece.endswith("/"):
        return f"{piece.rstrip('/')}/**"

    candidate = (base_dir / piece) if not Path(piece).is_absolute() else Path(piece)
    if candidate.is_dir():
        return f"{piece}/**"

    if "/" in piece or "." in piece:
        return piece

    return f"**/*.{piece}"


def expand_user_patterns(patterns: List[str], base_dir: Path) -> List[str]:
    """Expand user-friendly pattern shorthand into gitignore-style globs.

    Rules, applied per comma-split piece:
    - explicit globs (`**/*.py`, `src/[a-z]*.py`) pass through
    - existing directories (or trailing-slash) become `dir/**`
    - tokens containing `/` or `.` pass through (filenames, paths)
    - bare alphanumeric tokens become `**/*.{token}` (extension shortcut)
    """
    out: List[str] = []
    for raw in patterns:
        for piece in raw.split(","):
            expanded = _expand_one(piece, base_dir)
            if expanded:
                out.append(expanded)
    return out
