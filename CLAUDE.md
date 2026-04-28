# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repo.

## What this is

`llmfiles` is a Python CLI that packages a directory (or a GitHub repo URL) into a single LLM-friendly text blob. Beyond plain concatenation it offers:

- Tree-sitter chunking into functions/classes (`--chunk-strategy structure`).
- Python AST-based import tracing (`--deps`, optionally `--deps --all`) to pull in just the dependencies a seed file actually uses.
- Friendly include/exclude shorthand (bare extensions, dirs, comma lists).

User-facing behaviour and CLI examples live in `README.md`. Don't duplicate them here.

## Dev commands

```bash
uv sync --extra dev          # install deps (incl. ruff/mypy/pytest-cov)
uv run pytest                # full test suite
uv run pytest tests/test_pattern_expansion.py -v
uv run ruff check llmfiles/
uv run mypy llmfiles/
```

Always go through `uv` — never bare `python`, `pip`, or `pytest`. Never edit `uv.lock` by hand.

## Architecture

Top-level package: `llmfiles/`

- `cli/interface.py` — Click entrypoint. Parses flags, clones GitHub URLs into temp dirs, builds `PromptConfig`, hands off to `PromptGenerator`.
- `config/settings.py` — `PromptConfig` dataclass + enums (`ChunkStrategy`, `ExternalDepsStrategy`, `OutputFormat`).
- `core/`
  - `pipeline.py` — `PromptGenerator` orchestrates discovery → processing → templating → output.
  - `github.py` — `is_github_url`, `clone_github_repo` (shallow). CLI cleans the temp dir in a `finally`.
  - `output.py` — stdout / file writers.
  - `import_tracer.py` — pure-AST import walk for Python. Finds lazy imports inside functions, supports src-layout and relative imports, skips venv/`__pycache__`/`node_modules`. Smart symbol filtering only follows imports for symbols actually referenced.
  - `discovery/`
    - `walker.py` — `discover_paths` (file walk, gitignore, hidden, git-since, include/exclude) and `grep_files_for_content`.
    - `pattern_expansion.py` — turns user shorthand into gitignore globs (`py` → `**/*.py`, `scripts` → `scripts/**`, `py,md` → both). Applied to both `-i` and `-e`.
    - `pattern_matching.py` — gitignore + glob compilation via `pathspec`.
    - `path_resolution.py`, `git_utils.py`, `dependency_resolver.py` — supporting helpers.
- `structured_processing/` — tree-sitter parsers (`python_parser.py`, `javascript_parser.py`, `ast_utils.py`).

## Design notes

- File-first by default; structure-aware chunking is opt-in.
- Dependency expansion has two modes: `-r/--recursive` (simple import resolver) and `--deps` (AST tracer with optional `--all` to disable symbol filtering). `--trace-calls` is a deprecated alias for `--deps --all`.
- Discovery is gitignore-aware unless `--no-ignore`.
- GitHub URLs become regular local paths post-clone, so the rest of the pipeline doesn't care.

## Workflow when changing things

1. Read this file for orientation; check `spec.md` for existing test coverage in the area you touch.
2. TDD: red → green → refactor. New tests update `spec.md`.
3. If user-facing CLI behaviour changes: update help text in `cli/interface.py`, examples in `README.md`, and add a `CHANGELOG.md` entry under a new minor version (semver, no dates, no major bumps without asking).
4. If architecture changes: update this file.
5. `uv run pytest` before considering it done.

Single source of truth: this file = architecture, `README.md` = user docs, `spec.md` = test coverage, `CHANGELOG.md` = releases. Don't duplicate.
