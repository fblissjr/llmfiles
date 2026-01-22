# Changelog

All notable changes to this project will be documented in this file.

## 0.11.0

### Added
- New `--deps` flag for smart import tracing with unused symbol filtering
  - Only follows imports for symbols that are actually used in code
  - Significantly reduces output size by excluding unused imports
  - Example: `llmfiles entry.py --deps` (smart filtering)
  - Example: `llmfiles entry.py --deps --all` (no filtering, all imports)
- New `--all` flag to disable smart filtering when used with `--deps`
  - Use when smart filtering is too aggressive and misses needed files
  - Equivalent to the previous `--trace-calls` behavior

### Changed
- `--trace-calls` is now an alias for `--deps --all` (backward compatible)
  - Existing scripts using `--trace-calls` will continue to work unchanged
  - New projects should prefer `--deps` for smarter, smaller output

### Internal
- Added `SymbolUsageVisitor` class for tracking symbol references in Python code
- Added `ImportedSymbol` dataclass for tracking imported symbols
- Extended `ImportInfo` with `names` and `is_star` fields for symbol-level filtering
- Added `filter_unused` parameter to `CallTracer` for optional symbol filtering
- Added `follow_deps` and `filter_unused_imports` config fields
- Added comprehensive tests for symbol filtering functionality

## 0.10.0

### Added
- New `--format` CLI option to choose output format
  - `compact` (new default): LLM-optimized format with file index table first, then code, then dependency graph at end
  - `verbose`: Legacy format with full metadata upfront for backward compatibility
- File index table in compact format showing size, line count, and description for each file
- Module docstring extraction: Python files with docstrings now show first line as description in file index
- `line_count` field added to all processed elements

### Changed
- **Breaking**: Default output format changed from verbose to compact
  - Compact format puts code content before metadata (reduces context pollution for LLMs)
  - Dependency graph moved to end of output (was at beginning in verbose format)
  - File headers simplified: `### path/to/file.py (N lines)` instead of verbose element metadata
  - Use `--format verbose` to get the previous format

### Improved
- LLM context efficiency: Reduced metadata overhead before actual code content
- File prioritization: File index table helps LLMs understand codebase structure at a glance

## 0.9.0

### Added
- `--trace-calls` option for import tracing using AST parsing (Python only)
  - Traces all imports from entry files to build a complete dependency graph
  - Finds all imports including lazy imports inside functions
  - Supports src-layout projects (automatically adds src/, lib/, source/ to search paths)
  - Handles relative imports (`.module`, `..module`)
  - Fast and reliable - pure AST parsing, no code execution
  - Automatically excludes virtual environments, __pycache__, and node_modules
  - Generates an import dependency graph showing relationships between files
  - Example: `llmfiles main.py --trace-calls`

### Changed
- Replaced Jedi-based call tracing with AST-based import tracing
  - Previous Jedi implementation would hang on files importing heavy dependencies (e.g., torch)
  - AST approach is instant and doesn't require loading any modules

### Internal
- Renamed `jedi_tracer.py` to `import_tracer.py` and rewrote to use pure AST parsing
- Added `ImportInfo` dataclass for import information
- Added `resolve_relative_import()` for handling relative imports
- Removed `jedi` dependency - no longer needed

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
