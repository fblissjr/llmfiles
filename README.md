# llmfiles
yet another code & files to llm-optimized prompt input format. `llmfiles` automates the annoying parts of gathering files, directories (recursively), git context - and then formats it all into a single markdown / text file suitable for large language models. you can basically use this to manually provide the context you want at the granularity you want. you could also use this as a utility within MCP servers to provide context more dynamically. this might be where this project goes in the future. ~~likely to integrate~~ integrated tree-sitter into it as well.

## features
-   **flexible file selection:** 
    -   include/exclude files and directories using glob patterns (`--include`, `--exclude`).
    -   load include/exclude patterns from files for complex selections (`--include-from-file <path>`, `--exclude-from-file <path>`).
-   **`.gitignore` aware:** respects your project's `.gitignore` files by default (`--no-ignore` to disable).
-   **hidden file support:** optionally include hidden files and directories (`--hidden`).
-   **symlink following:** optionally traverse symbolic links (`--follow-symlinks`).
-   **templating engine:** uses handlebars for full control over output format. provide your own template (`--template`) or use built-in presets (`--preset`).
-   **git integration:**
    -   include staged diffs (`--diff`).
    -   include diffs between branches (`--git-diff-branch <base> <compare>`).
    -   include commit logs between branches (`--git-log-branch <base> <compare>`).
-   **source tree visualization:** includes a text-based directory tree in the output.
-   **yaml content processing:** optionally truncate long fields (binary or string) in yaml files at specific paths (`--yaml-truncate-long-fields`). (requires `pyyaml` to be installed: `pip install llmfiles[yaml_tools]`).
-   **token counting:** estimate token count for common models (`--show-tokens` for main output, `--console-tokens` for stderr).
-   **stdin piping:** accepts file paths from stdin for integration with `find`, `git ls-files`, etc. (`--stdin`, `-0` for null-separated).
-   **output options:** print to stdout (default), write to file (`-o`), copy to clipboard (`--clipboard`).
-   **customizable formatting:** 
    - line numbers (`-n`).
    - code block control (`--no-codeblock`).
    - control project root path display in header (`--show-abs-project-path`).
    - use absolute file paths for listed files (`--absolute-paths`).
-   **sorting:** sort included files by name or modification date (`--sort`).
-   **configuration files & profiles:** 
    - set persistent defaults and define reusable option sets (`--config-profile <name>`).
    - save current CLI options to a new or existing profile in the project-local `.llmfiles.toml` (`--save <PROFILE_NAME>`). Use `DEFAULT` as profile name to save as top-level defaults.
-   **console feedback control:** customize summary information printed to stderr during execution (`--console-summary`, `--console-tree`, `--console-tokens`).
-   **structured logging:** uses `structlog` for detailed, parsable logs, with an option for json output (`--force-json-logs`).

## installation

`llmfiles` is a python cli tool. python 3.11+ is recommended. it uses `toml` for configuration files and `structlog` for logging, which are installed by default. `pyyaml` is an optional dependency for yaml processing features.

**recommended: using `uv` (macos/linux)**
`uv` is a fast python package installer and resolver.

1. **install `llmfiles`:**
    *from source (after cloning this repository):*
    ```bash
    cd path/to/llmfiles_repo
    # install base tool
    uv pip install -e . -u
    # to include optional yaml processing features:
    uv pip install -e ".[yaml_tools]" -u
    ```

after installation, `llmfiles` should be available as a command in your terminal. verify with:
```bash
llmfiles --version
```

## basic usage

the core command structure is:
`llmfiles [options] [--input-path <path1> ...] [--stdin]`

if no `--input-path` is specified and `--stdin` is not used, `llmfiles` defaults to processing the current directory (`.`).

**example 1: process current directory, default markdown output, relative project path in header**
```bash
llmfiles
# or explicitly:
llmfiles --input-path .
# Output header will show: project root: current_dir_name
```

**example 2: show absolute project path in header**
```bash
llmfiles --show-abs-project-path
# Output header will show: project root: current_dir_name
# (full absolute path: /path/to/your/project)
```

**example 3: include only python files from patterns file, exclude tests, output to a file**
Create `my_py_includes.txt`:
```text
# my_py_includes.txt
src/**/*.py
utils/*.py
```
Then run:
```bash
llmfiles --input-path . --include-from-file my_py_includes.txt --exclude "**/tests/**" -o prompt.txt
```

**example 4: process specific files, a directory, and truncate long yaml fields**
```bash
# ensure pyyaml is installed: pip install llmfiles[yaml_tools]
llmfiles --input-path ./src/main.py ./docs ./my_vcr_cassettes.yaml \
         --yaml-truncate-long-fields --yaml-max-len 200 \
         -o project_context.txt
```

**example 5: use a preset template for claude, include git diff, and copy to clipboard**
```bash
llmfiles --input-path . --preset claude-optimal --diff --clipboard
```
this copies the claude-optimized prompt with the current staged git changes to your clipboard.

**example 6: pipe file list from `find` (e.g., recent rust files)**
```bash
find . -type f -name '*.rs' -mtime -2 -print0 | llmfiles --stdin -0
```
the `-print0` for `find` and `-0` for `llmfiles` handle filenames with spaces or special characters correctly.

**example 7: use a configuration profile**
(see "configuration file & profiles" section below for how to set up `my_python_review_profile`)
```bash
llmfiles --input-path ./my_project --config-profile my_python_review_profile
```

**example 8: save current settings to a profile**
```bash
llmfiles --include "**/*.py" --line-numbers --preset claude-optimal --save py_claude_review
# This saves the include, line_numbers, and preset settings to a profile named 
# 'py_claude_review' in '.llmfiles.toml' (or 'llmfiles.toml') in the current directory.
# The command then exits without generating a prompt.
```
To save as top-level defaults instead of a named profile, use `DEFAULT` as the profile name:
```bash
llmfiles --output-format xml --sort name_desc --save DEFAULT
```

## configuration file & profiles

you can set default options and define reusable "profiles" for `llmfiles` in a toml configuration file. `llmfiles` looks for configuration in this order (later files override earlier ones if settings conflict):
1.  user-global: `~/.config/llmfiles/config.toml`
2.  project-local: `.llmfiles.toml` or `llmfiles.toml` in the current working directory.
3.  project-local: `pyproject.toml` in the current working directory (settings under `[tool.llmfiles]` table).

cli arguments always take the highest precedence, overriding any settings from config files or profiles.

**example configuration file (`.llmfiles.toml` or `~/.config/llmfiles/config.toml`):**
```toml
# default settings applied if not overridden by a profile or cli arguments
# keys generally match the long cli option names (snake_case)

# global defaults
encoding = "cl100k" 
sort = "date_desc"   
line_numbers = true    
console_show_summary = true 
show_absolute_project_path = false # Default is false, explicit here
# yaml_truncate_long_fields = true # Example if you want this globally
# yaml_max_len = 300             

# define named profiles under the [profiles] table
[profiles.python_review]
description = "profile for reviewing python code changes."
include_patterns = ["**/*.py", "*.md"] # direct patterns
exclude_patterns = ["**/__pycache__/**", "**/dist/**", "*.egg-info/**"]
git_diff = true 
preset = "default" 
console_show_tokens = true 
output_file = "python_review_prompt.md"
show_absolute_project_path = true # Override global for this profile

[profiles.claude_api_gen]
description = "profile for generating api client code with claude."
preset = "claude-optimal"
include_patterns = ["src/api_spec.json", "src/core_logic/**/*.py"]
# yaml_truncate_long_fields = false # explicitly disable for this profile
vars = { project_name = "my awesome api", target_language = "python" }

[profiles.docs_and_scripts]
description = "Include all markdown and python scripts from specific pattern files."
# Assuming 'patterns/docs_includes.txt' and 'patterns/scripts_includes.txt' exist
# relative to this .llmfiles.toml file.
include_from_files = ["patterns/docs_includes.txt", "patterns/scripts_includes.txt"]
exclude_from_files = ["patterns/common_excludes.txt"]
line_numbers = true
```

**using a profile:**
```bash
# processes ./my_app_dir using settings from the 'python_review' profile
llmfiles --input-path ./my_app_dir --config-profile python_review

# override a profile setting with a cli argument
llmfiles --input-path . --config-profile python_review --output_file custom_output.txt --no-line-numbers
```

**available config keys (under general section or a profile):**
`input_paths` (list of strings), `include_patterns` (list of strings), `exclude_patterns` (list of strings), `include_from_files` (list of file paths as strings), `exclude_from_files` (list of file paths as strings), `no_ignore` (bool), `hidden` (bool), `follow_symlinks` (bool), `template` (string path), `preset` (string, e.g., "claude-optimal"), `vars` (table/dict), `output_format` (string), `line_numbers` (bool), `no_codeblock` (bool), `absolute_paths` (bool for file paths in list), `show_absolute_project_path` (bool for header), `yaml_truncate_long_fields` (bool), `yaml_placeholder` (string), `yaml_max_len` (int), `sort` (string, e.g. "name_asc"), `git_diff` (bool), `git_diff_branch` (list of two strings, e.g. `["main", "develop"]`), `git_log_branch` (list of two strings), `encoding` (string), `show_tokens_format` (string, "human" or "raw"), `output_file` (string path), `clipboard` (bool), `console_show_tree` (bool), `console_show_summary` (bool), `console_show_token_count` (bool).

refer to `llmfiles --help` for the corresponding cli option names. the config file keys are typically the long option name (with hyphens replaced by underscores).

## console output preferences

control what summary information `llmfiles` prints to your console (stderr) during execution:
*   `--console-tree` / `--no-console-tree`: show/hide project structure tree. (default: on)
*   `--console-summary` / `--no-console-summary`: show/hide file count summary. (default: on)
*   `--console-tokens` / `--no-console-tokens`: show/hide estimated token count for the generated prompt. (default: off)
these can also be set in the config file (e.g., `console_show_tree = false`).

## output formats & llm optimization

`llmfiles` uses handlebars templates to generate its output. you can control this with:

*   `--output-format <format>`: uses a basic built-in template if no preset or custom template is given. `<format>` can be `markdown` (default), `xml`, or `json`.
*   `--preset <name>`: uses a built-in, named preset template. current presets:
    *   `default`: general-purpose markdown output, good for many models (gpt-4, gemini).
    *   `claude-optimal`: structured xml output, based on anthropic's recommendations for claude models. uses `raw_content` for files.
    *   `generic-xml`: a simpler xml structure than `claude-optimal`.
*   `--template <path/to/your.hbs>`: **gives you full control.** use your own handlebars template file to structure the output exactly as needed for any llm or task. this overrides `--output-format` and `--preset`.

**general recommendations:**

*   **claude models (anthropic):**
    *   often benefit from xml-structured prompts, especially for long context.
    *   use `--preset claude-optimal` or a custom xml template.
    ```bash
    llmfiles --input-path . --preset claude-optimal --include "src/**/*.py" > claude_prompt.xml
    ```
*   **API models (openai), gemini models (google), and other general llms:**
    *   usually work well with well-structured markdown.
    *   `--preset default` (or no preset/format flag) is a good starting point.
    *   if they provide specific formatting advice (e.g., json input for function calling), create a custom template (`--template`) and potentially use `--output-format json` if your template generates the content for a json field.
    ```bash
    llmfiles --input-path ./my_project --preset default --diff > gpt4_prompt.md
    ```
*   **custom needs:** always use `--template your_template.hbs` for precise control over the output structure.

## handling very large files / context windows

if a single file or the total collection of files exceeds an llm's context window, `llmfiles` itself doesn't currently chunk or summarize. it focuses on collecting and formatting the specified content. the `--yaml-truncate-long-fields` option can help with large yaml files.

**strategies for large content:**

1.  **be more selective with filters:**
    *   use more specific `--include` and `--exclude` patterns (or `--include-from-file`/`--exclude-from-file`) to narrow down the most relevant files.
    *   if using git, focus on diffs (`--diff`, `--git-diff-branch`) which are usually smaller than entire files.
    *   use `find` with date/size criteria piped to `llmfiles --stdin` to select only recent or smaller files.
    ```bash
    llmfiles --input-path ./src/critical_module/ --include="relevant_file.py" -o focused_prompt.txt
    ```

2.  **manual chunking (pre-processing or multi-shot):**
    *   if a single file is too large (e.g., a massive log or data file), you might need to pre-process it outside `llmfiles` to extract relevant sections.
    *   for a large codebase, you might generate prompts for different sub-components separately using specific `--input-path` and filters for each.

3.  **use tools designed for summarization/chunking:**
    for tasks requiring summarization of very large individual documents before inclusion, consider dedicated summarization models/tools as a pre-processing step. `llmfiles` can then include these summaries.

`llmfiles` helps you get the *right raw material* into the prompt. managing extreme context sizes often requires higher-level strategies. check your target llm's token limits (use `--show-tokens` or `--console-tokens`) to guide your filtering.

## using with local llms (e.g., `mlx-lm` on macos)

`llmfiles` is designed to work seamlessly with local llm tools like `mlx-lm` via standard shell piping. generate your prompt with `llmfiles` and pipe it directly to your llm command.

**example with `mlx-lm`:**
```bash
# generate a prompt including python files from 'src', using the default markdown preset
# then pipe the result directly to mlx_lm.generate
llmfiles --input-path ./src --include "**/*.py" \
| python -m mlx_lm.generate --model <your_mlx_model_path_or_name> --max-tokens 1000
```

**example with claude-optimal preset, git diff, and user variable for `mlx-lm`:**
```bash
llmfiles --input-path . --preset claude-optimal --diff --var task_description="review this code for potential bugs" \
| python -m mlx_lm.generate --model <your_mlx_model_path_or_name> --temp 0.7
```

## all options
run `llmfiles --help` for a full list of command-line options and their defaults.

## templating context variables

when using `--template`, the following variables are available in your handlebars template:

*   `project_root_display_name`: name of the main input directory (e.g., `my_project`).
*   `project_root_path_absolute`: full absolute path of the main input directory.
*   `project_path_header_display`: path for the main header display (respects `show_absolute_project_path` setting).
*   `show_absolute_project_path`: boolean, true if the full absolute path should be detailed in the header.
*   `source_tree`: string representation of the filtered directory structure.
*   `files`: an array of file objects. each file object contains:
    *   `path`: string, file path (respects `--absolute-paths` for this list).
    *   `relative_path`: string, file path always relative to `project_root_display_name`.
    *   `content`: string, processed file content (line numbers, code blocks).
    *   `raw_content`: string, original unprocessed file content (after BOM stripping, UTF-8 decoding, and potential YAML truncation).
    *   `extension`: string, file extension (lowercase, no leading dot).
    *   `mod_time`: float, unix timestamp of last modification.
*   `git_diff`: string, staged git changes if `--diff` is used.
*   `git_diff_branches`: string, diff between branches if `--git-diff-branch` is used.
*   `git_diff_branch_base`, `git_diff_branch_compare`: strings, branch names for the diff.
*   `git_log_branches`: string, log between branches if `--git-log-branch` is used.
*   `git_log_branch_base`, `git_log_branch_compare`: strings, branch names for the log.
*   `user_vars`: dictionary from `--var key=value`.
*   `claude_indices`: dictionary for `claude-optimal` preset.
*   `{{now}}`: helper for current ISO timestamp (UTC).
*   `{{add val1 val2 ...}}`: helper to sum numbers.
*   `{{get_lang_hint extension_string}}`: helper for language hint from extension.

refer to the built-in templates in `llmfiles/templating.py` for examples.
