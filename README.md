# llmfiles
yet another code & files to llm-optimized prompt input format. `llmfiles` intelligently gathers specified files, directories, and git context, then formats it all into a single text block suitable for large language models.

## why use this?
modern llms have large context windows. feeding them relevant code, documentation, and diffs directly is often more effective than relying on vector databases or other indirect methods for tasks like code generation, review, or summarization. `llmfiles` automates the tedious process of collecting and formatting this context. it's built to be robust, flexible, and scriptable.

## features
-   **flexible file selection:** include/exclude files and directories using glob patterns.
-   **`.gitignore` aware:** respects your project's `.gitignore` files by default (`--no-ignore` to disable).
-   **hidden file support:** optionally include hidden files and directories (`--hidden`).
-   **symlink following:** optionally traverse symbolic links (`--follow-symlinks`).
-   **templating engine:** uses handlebars for full control over output format. provide your own template (`--template`) or use built-in presets (`--preset`).
-   **git integration:**
    -   include staged diffs (`--diff`).
    -   include diffs between branches (`--git-diff-branch <base> <compare>`).
    -   include commit logs between branches (`--git-log-branch <base> <compare>`).
-   **source tree visualization:** includes a text-based directory tree in the output.
-   **token counting:** estimate token count for common models (`--show-tokens --encoding <model_alias>`).
-   **stdin piping:** accepts file paths from stdin for integration with `find`, `git ls-files`, etc. (`--stdin`, `-0` for null-separated).
-   **output options:** print to stdout (default), write to file (`-o`), copy to clipboard (`--clipboard`).
-   **customizable formatting:** line numbers (`-n`), code block control (`--no-codeblock`), absolute/relative paths (`--absolute-paths`).
-   **sorting:** sort included files by name or modification date (`--sort`).

## installation

`llmfiles` is a python cli tool. python 3.11+ is recommended.

**recommended: using `uv` (macos/linux)**
`uv` is a fast python package installer and resolver.

1.  **install `uv`:**
    ```bash
    # on macos using homebrew
    brew install uv

    # or on macos/linux using curl
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
    follow the instructions to add `uv` to your path if needed.

2.  **install `llmfiles`:**
    once `uv` is installed, you can install `llmfiles` directly from its source (if you've cloned the repo) or from pypi (once published).

    *from source (after cloning this repository):*
    ```bash
    cd path/to/llmfiles_repo
    uv pip install -e . -U
    ```

**alternative: using `pip`**

1.  **ensure you have python 3.11+ and pip.**
2.  **install `llmfiles`:**

    *from source (after cloning this repository):*
    ```bash
    cd path/to/llmfiles
    pip install -e . -U
    ```

after installation, `llmfiles` should be available as a command in your terminal. verify with:
```bash
llmfiles --version
```

## basic usage

the core command structure is:
`llmfiles [paths_or_stdin_options] [filtering_options] [formatting_options] [output_options]`

**example 1: process current directory, default markdown output**
```bash
llmfiles .
```

**example 2: include only python files, exclude tests, output to a file**
```bash
llmfiles . --include '*.py' --exclude '**/tests/**' -o prompt.txt
```

**example 3: process specific files and a directory**
```bash
llmfiles ./src/main.py ./docs ./README.md -o project_context.txt
```

**example 4: use a preset template for claude and include git diff**
```bash
llmfiles . --preset claude-optimal --diff --clipboard
```
this copies the claude-optimized prompt with the current staged git changes to your clipboard.

**example 5: pipe file list from `find` (e.g., files modified in last 2 days)**
```bash
find . -type f -name '*.rs' -mtime -2 -print0 | llmfiles --stdin -0
```
the `-print0` for `find` and `-0` for `llmfiles` handle filenames with spaces or special characters correctly.

## output formats & llm optimization

`llmfiles` uses handlebars templates to generate its output. you can control this with:

*   `--output-format <format>`: uses a very basic built-in template. `<format>` can be `markdown` (default) or `xml`. this is a fallback if no specific preset or template is given.
*   `--preset <name>`: uses a built-in, named preset template. current presets:
    *   `default`: general-purpose markdown output, good for many models (gpt-4, gemini).
    *   `claude-optimal`: structured xml output, based on anthropic's recommendations for claude models. uses `raw_content` for files.
    *   `generic-xml`: a simpler xml structure than `claude-optimal`.
*   `--template <path/to/your.hbs>`: **gives you full control.** use your own handlebars template file to structure the output exactly as needed for any llm or task. this overrides `--output-format` and `--preset`.

**general recommendations:**

*   **claude models (anthropic):**
    *   often benefit from xml-structured prompts, especially for long context.
    *   use `--preset claude-optimal` as a starting point.
    *   for optimal performance, refer to anthropic's latest documentation and craft a custom template if needed using `--template`.
    ```bash
    llmfiles . --preset claude-optimal --include 'src/**/*.py' > claude_prompt.xml
    ```
*   **gpt models (openai), gemini models (google), and other general llms:**
    *   usually work well with well-structured markdown.
    *   `--preset default` (or no preset/format flag) is a good starting point.
    *   if they provide specific formatting advice (e.g., json input for function calling), create a custom template.
    ```bash
    llmfiles ./my_project --preset default --diff > gpt4_prompt.md
    ```
*   **custom needs:** always use `--template your_template.hbs` for precise control.

## handling very large files / context windows

if a single file or the total collection of files exceeds an llm's context window, `llmfiles` itself doesn't currently chunk or summarize. it focuses on collecting and formatting the specified content.

**strategies for large content:**

1.  **be more selective with filters:**
    *   use more specific `--include` and `--exclude` patterns to narrow down the most relevant files.
    *   if using git, focus on diffs (`--diff`, `--git-diff-branch`) which are usually smaller than entire files.
    *   use `find` with date/size criteria piped to `llmfiles --stdin` to select only recent or smaller files.
    ```bash
    # example: only include python files under 500 lines from the src directory
    # (requires a tool like 'cloc' or custom script for line count filtering before piping to llmfiles)
    # or more simply, just be very specific with paths:
    llmfiles ./src/critical_module_one/ ./src/critical_module_two/relevant_file.py -o focused_prompt.txt
    ```

2.  **manual chunking (pre-processing or multi-shot):**
    *   if a single file is too large (e.g., a massive log or data file), you might need to pre-process it outside `llmfiles` to extract relevant sections.
    *   for a large codebase, you might generate prompts for different sub-components separately and feed them to the llm in sequence or as part of a multi-shot conversation.
    ```bash
    # prompt for backend
    llmfiles ./backend --exclude '**/tests/**' -o backend_prompt.txt
    # prompt for frontend
    llmfiles ./frontend --include '*.ts' --exclude '**/*.spec.ts' -o frontend_prompt.txt
    # then use backend_prompt.txt and frontend_prompt.txt with your llm
    ```

3.  **use tools designed for summarization/chunking:**
    for tasks requiring summarization of very large individual documents before inclusion, consider dedicated summarization models/tools as a pre-processing step. `llmfiles` can then include these summaries.

`llmfiles` helps you get the *right raw material* into the prompt. managing extreme context sizes often requires higher-level strategies. check your target llm's token limits (`llmfiles --show-tokens`) to guide your filtering.

## using with local llms (e.g., `mlx-lm` on macos)

`llmfiles` is designed to work seamlessly with local llm tools like `mlx-lm` via standard shell piping. generate your prompt with `llmfiles` and pipe it directly to your llm command.

**example with `mlx-lm`:**
```bash
# generate a prompt including python files from 'src', using the default markdown preset
# then pipe the result directly to mlx_lm.generate
llmfiles ./src --include '*.py' \
| python -m mlx_lm.generate --model <your_mlx_model_path_or_name> --max-tokens 1000
```

**example with claude-optimal preset, git diff, and user variable for `mlx-lm`:**
```bash
llmfiles . --preset claude-optimal --diff --var task_description="review this code for potential bugs" \
| python -m mlx_lm.generate --model <your_mlx_model_path_or_name> --temp 0.7
```

## all options
run `llmfiles --help` for a full list of command-line options.

## templating context variables

when using `--template`, the following variables are available in your handlebars template:

*   `project_root_display_name`: name of the main input directory.
*   `project_root_path_absolute`: full absolute path of the main input directory.
*   `source_tree`: string representation of the filtered directory structure.
*   `files`: an array of file objects. each file object contains:
    *   `path`: string, file path (absolute if `--absolute-paths`, otherwise relative to `project_root_display_name`).
    *   `relative_path`: string, file path always relative to `project_root_display_name`.
    *   `content`: string, file content processed by `--line-numbers` and `--no-codeblock`.
    *   `raw_content`: string, original unprocessed file content.
    *   `extension`: string, file extension (lowercase).
    *   `mod_time`: float, unix timestamp of last modification (only present if sorting by date).
*   `git_diff`: string, output of staged git changes, if `--diff` is used.
*   `git_diff_branches`: string, output of diff between branches, if `--git-diff-branch` is used.
*   `git_diff_branch_base`, `git_diff_branch_compare`: strings, the branch names for the diff.
*   `git_log_branches`: string, output of log between branches, if `--git-log-branch` is used.
*   `git_log_branch_base`, `git_log_branch_compare`: strings, the branch names for the log.
*   `user_vars`: dictionary of variables passed via `--var key=value`.
*   `claude_indices`: dictionary with calculated indices for specific sections if `claude-optimal` preset is used (e.g., `claude_indices.source_tree_idx`).
*   `{{now}}`: helper that outputs the current iso timestamp.
*   `{{add val1 val2 ...}}`: helper that adds numerical values.

refer to the built-in templates in `llmfiles/templating.py` for examples.