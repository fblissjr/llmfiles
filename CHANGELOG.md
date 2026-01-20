# Changelog

All notable changes to this project will be documented in this file.

## 0.9.0

### Added
- `--trace-calls` option for semantic call tracing using Jedi (Python only)
  - Traces all function calls from entry files to build a complete call graph
  - More comprehensive than `--recursive` which only follows import statements
  - Automatically excludes virtual environments, __pycache__, and node_modules
  - Generates a call graph summary showing call relationships between files
  - Example: `llmfiles main.py --trace-calls`

### Internal
- Added `jedi_tracer.py` module in `core/` for Jedi-based call tracing
- Added `trace_calls` parameter to `PromptConfig`
- Added `jedi>=0.19.0` as a new dependency

## 0.8.0

### Added
- GitHub repository support: Accept GitHub URLs as input sources
  - Example: `llmfiles https://github.com/user/repo --include "**/*.py"`
  - Clones repo to temp directory, processes files, auto-cleans up
  - Supports public repositories only
  - Uses shallow clone (--depth=1) for faster cloning

### Changed
- Default chunk strategy changed from `structure` to `file`
  - `file` mode treats each file as a single chunk (simpler, no duplication)
  - `structure` mode still available via `--chunk-strategy structure`
  - Based on research of similar tools (Repomix, Aider, Continue.dev)

### Fixed
- Structure mode no longer duplicates method code
  - Previously: classes and their methods were output as separate elements (2x bloat)
  - Now: classes contain their methods, no separate method elements
  - Applies to both Python and JavaScript parsers
- Fixed enum comparison bug in external dependencies output

### Internal
- Added `github.py` module in `core/` for GitHub URL handling
- CLI now accepts paths as strings (not Path objects) to preserve URL format
- Added `base_dir` parameter support in `PromptConfig`

## 0.7.1

### Added
- `--git-since` option to filter files based on git modification date
  - Allows filtering files modified since a specific date (e.g., "7 days ago", "2025-01-01", "1 week ago")
  - Works with all other filtering options (include/exclude patterns, max-size, etc.)
  - Gracefully handles non-git repositories by logging a warning and continuing without git filtering

### Internal
- Added `git_utils.py` module in `core/discovery/` for git operations
- Integrated git filtering into the file discovery pipeline in `walker.py`
- Added `git_since` parameter to `PromptConfig`

## 0.7.0

### Initial Release
- Structure-aware chunking using tree-sitter for semantic code parsing
- Automatic dependency resolution for Python imports
- Support for multiple file patterns and content search
- File size filtering and binary file handling
- Configurable output strategies (structure vs file-based chunking)
- Git ignore support with override options
