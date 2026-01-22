# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

llmfiles is a Python CLI tool that intelligently packages code and text into a single file optimized for LLMs. It supports:
- **GitHub repository processing**: Clone and process public repos directly from URLs
- **Tree-sitter semantic parsing**: Optional structure-aware chunking for Python and JavaScript
- **Automatic dependency resolution**: Follow Python imports to build complete context
- **AST-based import tracing**: Trace all imports from entry files using pure AST parsing (Python only). Finds lazy imports, supports src-layout projects.

## Development Commands

### Installation and Setup
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On macOS/Linux

# Install package in development mode with all dependencies
uv pip install -e ".[dev]"
```

### Running Tests
```bash
# Run all tests
pytest

# Run tests with coverage
pytest --cov=llmfiles

# Run specific test file
pytest tests/test_processing.py

# Run specific test
pytest tests/test_processing.py::test_function_name
```

### Code Quality
```bash
# Run linting
ruff check llmfiles/

# Run type checking
mypy llmfiles/
```

### CLI Usage
```bash
# Basic usage - process current directory
llmfiles

# Process a GitHub repository directly
llmfiles https://github.com/user/repo
llmfiles https://github.com/user/repo --include "**/*.py"

# Process specific files with dependency resolution
llmfiles src/main.py -r

# Smart import tracing with unused symbol filtering (recommended)
llmfiles main.py --deps

# Trace ALL imports without filtering (when --deps misses something)
llmfiles main.py --deps --all

# Legacy alias for --deps --all (backward compatible)
llmfiles main.py --trace-calls

# Search for files containing specific content
llmfiles . --grep-content "pattern"

# Include specific file patterns
llmfiles --include "**/*.py"

# Use structure-aware chunking (extract functions/classes)
llmfiles --chunk-strategy structure --include "**/*.py"

# Exclude files larger than 1MB
llmfiles . --max-size 1MB

# Include binary files (excluded by default)
llmfiles . --include-binary

# Combine filters
llmfiles . --max-size 500KB --include "**/*.py" --exclude "**/test_*"
```

## Architecture

### Core Components

1. **Pipeline (llmfiles/core/pipeline.py)**
   - `PromptGenerator` class orchestrates the entire processing flow
   - Handles dependency resolution, file processing, and output formatting
   - Manages external dependency tracking

2. **GitHub Support (llmfiles/core/github.py)**
   - `is_github_url()`: Detects GitHub repository URLs
   - `clone_github_repo()`: Clones repos to temp directories using shallow clone
   - Auto-cleanup after processing via CLI finally block

3. **Discovery Module (llmfiles/core/discovery/)**
   - `walker.py`: File system traversal with gitignore support
   - `dependency_resolver.py`: Python import resolution and dependency tracking
   - `pattern_matching.py`: Glob pattern matching for file selection
   - `path_resolution.py`: Path normalization and resolution
   - `git_utils.py`: Git command execution for --git-since filtering

4. **Import Tracer (llmfiles/core/import_tracer.py)**
   - AST-based import tracing for Python files
   - Finds all imports including lazy imports inside functions
   - Supports src-layout projects and relative imports
   - Fast and reliable - pure AST parsing, no code execution
   - Automatically excludes venvs, __pycache__, and node_modules
   - Smart symbol filtering: only follows imports for symbols actually used in code

5. **Structured Processing (llmfiles/structured_processing/)**
   - Language-specific parsers using tree-sitter
   - `python_parser.py`: Extracts functions, classes from Python files
   - `javascript_parser.py`: Handles JavaScript/TypeScript files
   - `ast_utils.py`: Common AST manipulation utilities

6. **CLI Interface (llmfiles/cli/interface.py)**
   - Click-based command structure
   - Handles file paths, GitHub URLs, and stdin input
   - Progress reporting via Rich library

### Key Design Patterns

- **File-first Chunking**: By default, files are included as complete units (simpler, no duplication)
- **Optional Semantic Chunking**: Use `--chunk-strategy structure` to parse into functions/classes
- **Dependency Graph Building**: Starting from seed files, follows imports to build complete context
- **Import Tracing**: Use `--deps` for smart import tracing with unused symbol filtering, or `--deps --all` to trace all imports (finds lazy imports, supports src-layout)
- **Remote Source Support**: GitHub URLs are cloned to temp directories and processed like local paths
- **Stream Processing**: Designed for Unix-style composability with pipes

### Configuration

- `.llmfiles.toml`: Project-specific profiles and patterns
- Respects `.gitignore` by default (can be overridden with `--no-ignore`)

### Documentation

- `spec.md`: Test coverage specification - the living checklist of what's tested
- `CHANGELOG.md`: User-facing changes (semantic versioning, no dates)

## Development Workflow

When making changes to this codebase, follow this checklist:

### Before Starting
1. Read this file (CLAUDE.md) for architecture context
2. Check `spec.md` for existing test coverage of the area you're modifying

### When Adding Features
1. Write tests first (TDD) - update `spec.md` checklist as you go
2. Implement the feature
3. Update CLI help text if adding flags/options
4. Add entry to `CHANGELOG.md` under appropriate version heading

### When Fixing Bugs
1. Add a failing test that reproduces the bug
2. Fix the bug
3. Update `spec.md` if test coverage changed
4. Add entry to `CHANGELOG.md` if user-facing

### After Completing Work
Always update these files (check each one):
- [ ] `spec.md` - Mark new tests as `[x]`, add new test items
- [ ] `CHANGELOG.md` - Document user-facing changes
- [ ] `CLAUDE.md` - Update if architecture/CLI usage changed
- [ ] Run `uv run pytest` to verify all tests pass

### Key Principle
**Single source of truth**: Don't duplicate information across files. CLAUDE.md describes architecture, spec.md tracks test coverage, CHANGELOG.md tracks releases. Each file has one job.