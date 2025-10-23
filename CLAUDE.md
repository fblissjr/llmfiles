# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

llmfiles is a Python CLI tool that intelligently packages code and text into a single file optimized for LLMs. It uses tree-sitter for semantic code parsing and supports automatic dependency resolution.

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

# Process specific files with dependency resolution
llmfiles src/main.py

# Search for files containing specific content
llmfiles . --grep-content "pattern"

# Include specific file patterns
llmfiles --include "**/*.py"

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

2. **Discovery Module (llmfiles/core/discovery/)**
   - `walker.py`: File system traversal with gitignore support
   - `dependency_resolver.py`: Python import resolution and dependency tracking
   - `pattern_matching.py`: Glob pattern matching for file selection
   - `path_resolution.py`: Path normalization and resolution

3. **Structured Processing (llmfiles/structured_processing/)**
   - Language-specific parsers using tree-sitter
   - `python_parser.py`: Extracts functions, classes, imports from Python files
   - `javascript_parser.py`: Handles JavaScript/TypeScript files
   - `ast_utils.py`: Common AST manipulation utilities

4. **CLI Interface (llmfiles/cli/interface.py)**
   - Click-based command structure
   - Handles both file arguments and stdin input
   - Progress reporting via Rich library

### Key Design Patterns

- **Semantic Chunking**: Files are parsed into logical units (functions, classes) rather than arbitrary text chunks
- **Dependency Graph Building**: Starting from seed files, follows imports to build complete context
- **Configurable Strategies**: Supports different chunking strategies (structure vs file) and dependency handling modes
- **Stream Processing**: Designed for Unix-style composability with pipes

### Configuration

- `.llmfiles.toml`: Project-specific profiles and patterns
- Respects `.gitignore` by default (can be overridden with `--no-ignore`)