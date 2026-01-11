# llmfiles Test Coverage Specification

This document tracks the test coverage requirements and progress for llmfiles.

## Core Modules and Test Status

### 1. GitHub Support (`llmfiles/core/github.py`)
- [x] `is_github_url()` - URL detection
  - [x] Valid HTTPS URLs (https://github.com/user/repo)
  - [x] Valid URLs without scheme (github.com/user/repo)
  - [x] URLs with .git suffix
  - [x] Invalid URLs (gitlab, local paths, etc.)
- [x] `normalize_github_url()` - URL normalization
  - [x] Add https:// prefix when missing
  - [x] Strip trailing slashes
- [x] `clone_github_repo()` - Repository cloning
  - [x] Successful clone (mocked)
  - [x] Git not found error
  - [x] Clone failure error

### 2. CLI Interface (`llmfiles/cli/interface.py`)
- [x] End-to-end dependency resolution (fixed with mock)
- [x] End-to-end grep seed (fixed with mock)
- [ ] GitHub URL processing
  - [ ] Single GitHub URL input
  - [ ] Mixed local and GitHub paths
  - [ ] Cleanup on success
  - [ ] Cleanup on error
- [ ] File size parsing
  - [ ] Parse KB, MB, GB units
  - [ ] Invalid format handling

### 3. Processing (`llmfiles/core/processing.py`)
- [x] `process_file_content_to_elements()`
  - [x] File strategy - whole file as element
  - [x] Structure strategy - extract functions/classes
  - [x] Binary file detection and skip
  - [x] Max file size filtering
  - [x] Empty file handling
  - [x] Default chunk strategy is FILE
  - [x] Structure mode no longer duplicates methods

### 4. Dependency Resolution (`llmfiles/core/discovery/dependency_resolver.py`)
- [x] Simple imports extraction
- [x] From imports extraction
- [x] Dotted imports extraction
- [x] No imports case
- [x] Import aliases (fixed - updated tree-sitter query)
- [x] Internal file resolution
- [x] Internal package resolution
- [x] External package resolution
- [x] Stdlib resolution
- [x] Unresolved imports

### 5. Discovery (`llmfiles/core/discovery/`)
- [x] Grep files for content
- [x] Grep files no matches
- [ ] Pattern matching
  - [ ] Include patterns
  - [ ] Exclude patterns
  - [ ] Hidden files
- [ ] Git-based filtering
  - [ ] Files modified since date

### 6. Language Parsers (`llmfiles/structured_processing/language_parsers/`)
- [ ] Python parser
  - [ ] Extract functions
  - [ ] Extract classes (without duplicate methods)
  - [ ] Extract imports
- [ ] JavaScript parser
  - [ ] Extract functions
  - [ ] Extract classes
  - [ ] Arrow functions

### 7. Output (`llmfiles/core/output.py`)
- [ ] Write to stdout
- [ ] Write to file

## Known Failing Tests

All pre-existing failures have been fixed:

1. `test_cli_end_to_end_dependency_resolution` - FIXED
   - Root cause: `numpy` not in INSTALLED_PACKAGES, goes to "unresolved"
   - Fix: Added mock for INSTALLED_PACKAGES in tests to include numpy

2. `test_cli_end_to_end_grep_seed` - FIXED
   - Same issue as above, same fix applied

3. `test_extract_imports_with_aliases` - FIXED
   - Root cause: Tree-sitter query doesn't capture aliased imports
   - Fix: Updated import query in ast_utils.py to capture aliased_import

## Test Implementation Order

1. Fix existing failing tests
2. Add GitHub support tests
3. Add processing tests
4. Add parser tests
5. Add output tests
