# llmfiles

a developer-focused cli for packaging code into a single, llm-friendly text blob.

beyond plain concatenation: tree-sitter chunking, ast-based python import tracing, content search, and a pattern shorthand that doesn't make you fight your shell.

## install

```bash
uv pip install .
```

## quick start

```bash
llmfiles                              # current directory
llmfiles https://github.com/user/repo # remote repo (cloned to temp, cleaned up)
llmfiles -i py                        # all *.py files in cwd
llmfiles -i py,md -e CHANGELOG.md     # *.py and *.md, except changelog
llmfiles . -i py -e scripts -e tests  # *.py, excluding scripts/ and tests/
llmfiles . -e uv.lock                 # everything except uv.lock
llmfiles src/main.py --deps           # main.py + the imports it actually uses
```

## filter shorthand (`-i` / `-e`)

both flags accept the same lightweight syntax. each value can be:

| you write | becomes | meaning |
|---|---|---|
| `py` | `**/*.py` | bare extension |
| `py,md` | `**/*.py`, `**/*.md` | comma-separated list |
| `scripts` | `scripts/**` | existing directory |
| `scripts/` | `scripts/**` | trailing slash also works |
| `CHANGELOG.md` | `CHANGELOG.md` | filename (matched at any depth, gitignore-style) |
| `**/*.py` | unchanged | explicit glob passes through |

flags are repeatable, so `-e scripts -e tests` and `-e scripts,tests` are equivalent.

## examples

**process a github repo, python only, skip large files**

```bash
llmfiles https://github.com/user/repo -i py --max-size 100KB
```

**dependency-aware bundling (python)**

```bash
llmfiles src/main.py --deps          # main.py + only the symbols it uses
llmfiles src/main.py --deps --all    # main.py + everything it imports
```

`--deps` follows imports recursively using pure ast parsing (no execution), finds lazy imports inside functions, and respects src-layout. add `--all` if smart filtering misses something.

**find files by content, then bundle them**

```bash
llmfiles . --grep-content "qwen image edit"
```

selects files containing the string and uses them as seeds for dependency resolution.

**structure-aware chunking (functions/classes instead of whole files)**

```bash
llmfiles --chunk-strategy structure -i py
```

**combine with `find` for something dynamic**

```bash
find . -type f -name '*.js' -mtime -3 -print0 | llmfiles --stdin -0
```

**only files modified recently in git**

```bash
llmfiles . --git-since "7 days ago" -i py
llmfiles . --git-since "2025-01-01"
```

**list external dependencies a file pulls in**

```bash
llmfiles src/utils.py --external-deps metadata
```

**pipe to clipboard**

```bash
llmfiles src/main.py src/utils.py | pbcopy
```

## options

run `llmfiles --help` for the full list. the high-traffic flags:

- `-i, --include` / `-e, --exclude` — see shorthand table above; repeatable.
- `--deps` / `--deps --all` — python ast import tracing.
- `-r, --recursive` — simple import-based dependency expansion (lighter than `--deps`).
- `--grep-content TEXT` — content-based file selection.
- `--chunk-strategy [file|structure]` — file-level (default) or function/class-level chunks.
- `--max-size SIZE` — skip files larger than e.g. `1MB`, `500KB`.
- `--git-since DATE` — only files modified in git since the given date.
- `--include-binary` — binaries are skipped by default.
- `--no-ignore` — bypass `.gitignore`.
- `--hidden` — include dotfiles/dot-dirs.
- `-o, --output FILE` — write to file instead of stdout.
- `--stdin` / `-0` — read paths from stdin (nul-separated with `-0`).
- `--format [compact|verbose]` — output format. `compact` (default) is tuned for llm consumption.

## license

apache-2.0
