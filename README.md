# llmfiles

a developer-focused command-line tool to intelligently package code and text into a single file, optimized for large language models.

it moves beyond simple file concatenation by using tree-sitter to parse code into semantic chunks (functions, classes, etc.), providing llms with clean, labeled context.

## features

-   **github repository support:** process remote github repositories directly by passing a url - no manual cloning required.
-   **recursive dependency resolution:** for python, automatically finds and includes files that are imported by your seed files, providing much richer context.
-   **content-based file search:** use the `--grep-content` flag to select files based on a text pattern in their content, not just their file path.
-   **intelligent code chunking:** optionally parse supported languages (python, javascript) into logical units like functions and classes with `--chunk-strategy structure`.
-   **smart file filtering:** automatically excludes binary files and supports size-based filtering to skip large files.
-   **`.gitignore` aware:** respects your project's `.gitignore` files by default.
-   **flexible file selection:** include and exclude files using familiar glob patterns.
-   **standard unix-style composability:** designed to work with pipes and other standard cli tools like `find`, `sort`, and `xargs`.
-   **clean, llm-optimized output:** generates a structured markdown format that clearly labels each code element with its type, name, and source file.

## installation

```bash
uv pip install .
```

## usage

the core command structure is simple:
`llmfiles [options] [paths...]`

if no `paths` are specified, it processes the current directory. it can also read paths from stdin.

**example 1: process a github repository**

```bash
# process a public github repository directly
llmfiles https://github.com/user/repo

# include only python files from a github repo
llmfiles https://github.com/user/repo --include "**/*.py"

# limit file sizes when processing large repos
llmfiles https://github.com/user/repo --max-size 100KB --include "**/*.py"
```

the repository is cloned to a temp directory, processed, and automatically cleaned up.

**example 2: process all python files in the current project**

```bash
llmfiles --include "**/*.py"
```

this will include all python files and print the structured markdown to stdout.

**example 3: process specific files and pipe to a clipboard utility**

```bash
llmfiles ./src/main.py ./src/utils.py | pbcopy
```

**example 4: combine with `find` to process recently modified javascript files**

```bash
find . -type f -name '*.js' -mtime -3 -print0 | llmfiles --stdin -0
```
the `-print0` for `find` and `-0` for `llmfiles` handle filenames with spaces correctly.

**example 5: use structure-aware chunking**

by default, files are included as whole units. use `--chunk-strategy structure` to parse python and javascript into separate functions and classes.

```bash
llmfiles --chunk-strategy structure --include "src/**/*.py"
```

**example 6: automatic dependency resolution**

start with a single entrypoint file, and `llmfiles` will follow its internal imports to build a comprehensive context.

```bash
# main.py imports utils.py, which imports helpers.py
# llmfiles will automatically include all three files in the output.
llmfiles src/main.py
```

**example 7: find files by content (`grep`)**

you don't know the file name, but you know it contains the text "qwen image edit". use `--grep-content` to find it and all of its dependencies.

```bash
llmfiles . --grep-content "qwen image edit"
```

**example 8: list external dependencies**

to see which external libraries a file depends on, use `--external-deps metadata`.

```bash
llmfiles src/utils.py --external-deps metadata
```
this will add a list of packages like `numpy` or `pandas` to the output for that file.

**example 9: exclude large files**

skip files larger than a specified size to avoid including massive data files, logs, or compiled assets.

```bash
# exclude files larger than 1MB
llmfiles . --max-size 1MB

# exclude files larger than 500KB
llmfiles . --max-size 500KB
```

**example 10: include binary files**

by default, binary files (detected by UTF-8 decode errors) are excluded. to include them:

```bash
llmfiles . --include-binary
```

**example 11: only include recently modified files (git)**

filter files based on when they were last modified in git. useful for reviewing recent changes or creating context for recent work.

```bash
# files modified in the last 7 days
llmfiles . --git-since "7 days ago"

# files modified since a specific date
llmfiles . --git-since "2025-01-01"

# files modified in the last week
llmfiles . --git-since "1 week ago"

# combine with other filters
llmfiles . --git-since "3 days ago" --include "**/*.py"
```

## all options

```text
$ llmfiles --help
usage: llmfiles [options] [paths...]

  aggregate and format specified file content into a single text block.

  reads files from specified paths or from standard input. if no paths are
  given and stdin is not used, it processes the current directory.

options:
  -i, --include pattern   glob pattern for files to include. can be used
                          multiple times.
  -e, --exclude pattern   glob pattern for files to exclude. can be used
                          multiple times.
  --grep-content pattern  search file contents for a pattern and include
                          matching files as seeds for dependency resolution.
  --chunk-strategy [structure|file]
                          strategy for chunking files. 'file' (default)
                          treats each file as a single chunk. 'structure'
                          uses ast parsing for supported languages.
  --external-deps [ignore|metadata]
                          strategy for handling external dependencies: 'ignore'
                          or 'metadata'.
  --no-ignore             do not respect .gitignore files.
  --hidden                include hidden files and directories (starting with
                          a dot).
  --include-binary        include binary files (detected by UTF-8 decode
                          errors). by default, binary files are excluded.
  --max-size SIZE         exclude files larger than specified size (e.g.,
                          '1MB', '500KB', '10MB'). accepts units: B, KB, MB, GB.
  --git-since DATE        only include files modified in git since the
                          specified date (e.g., '7 days ago', '2025-01-01',
                          '1 week ago').
  -l, --follow-symlinks   follow symbolic links.
  -n, --line-numbers      prepend line numbers to file content.
  --no-codeblock          omit markdown code blocks around file content.
  -o, --output file       write output to file instead of stdout.
  --stdin                 read file paths from standard input.
  -0, --null              when using --stdin, paths are separated by a nul
                          character.
  -r, --recursive         recursively include all local code imported by the
                          seed files.
  -v, --verbose           enable verbose logging output to stderr.
  --version               show the version and exit.
  -h, --help              show this message and exit.
```

## license

apache-2.0
