# llmfiles

a developer-focused command-line tool to intelligently package code and text into a single file, optimized for large language models.

it moves beyond simple file concatenation by using tree-sitter to parse code into semantic chunks (functions, classes, etc.), providing llms with clean, labeled context.

## features

-   **intelligent code chunking:** automatically parses supported languages (python, javascript) into logical units like functions and classes.
-   **fallback to file chunking:** gracefully handles unsupported file types by treating them as a single element.
-   **`.gitignore` aware:** respects your project's `.gitignore` files by default.
-   **flexible file selection:** include and exclude files using familiar glob patterns.
-   **standard unix-style composability:** designed to work with pipes and other standard cli tools like `find`, `sort`, and `xargs`.
-   **clean, llm-optimized output:** generates a structured markdown format that clearly labels each code element with its type, name, and source file.

## installation

```bash
pip install .
```

## usage

the core command structure is simple:
`llmfiles [options] [paths...]`

if no `paths` are specified, it processes the current directory. it can also read paths from stdin.

**example 1: process all python files in the current project**

```bash
llmfiles --include "**/*.py"
```

this will parse all python files, break them down into function and class elements, and print the structured markdown to stdout.

**example 2: process specific files and pipe to a clipboard utility**

```bash
llmfiles ./src/main.py ./src/utils.py | pbcopy
```

**example 3: combine with `find` to process recently modified javascript files**

```bash
find . -type f -name '*.js' -mtime -3 -print0 | llmfiles --stdin -0
```
the `-print0` for `find` and `-0` for `llmfiles` handle filenames with spaces correctly.

**example 4: force whole-file processing for all files**

sometimes you want to disable semantic chunking and just get the content of each file.

```bash
llmfiles --chunk-strategy file --include "src/**/*.py"
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
  --chunk-strategy [structure|file]
                          strategy for chunking files. 'structure' (default)
                          uses ast parsing for supported languages. 'file'
                          treats each file as a single chunk.
  --no-ignore             do not respect .gitignore files.
  --hidden                include hidden files and directories (starting with
                          a dot).
  -l, --follow-symlinks   follow symbolic links.
  -n, --line-numbers      prepend line numbers to file content.
  --no-codeblock          omit markdown code blocks around file content.
  -o, --output file       write output to file instead of stdout.
  --stdin                 read file paths from standard input.
  -0, --null              when using --stdin, paths are separated by a nul
                          character.
  -v, --verbose           enable verbose logging output to stderr.
  --version               show the version and exit.
  -h, --help              show this message and exit.
```

## license

apache-2.0
