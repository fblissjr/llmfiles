# llmfiles
yet another code &amp; files to llm-optimized prompt input format

## using with local LLMs (such as `mlx-lm` on macos)
`llmfiles` is designed to work seamlessly with local LLM tools like `mlx-lm` via standard shell piping. Generate your prompt with `llmfiles` and pipe it directly to your LLM command.

1. Generate prompt using llmfiles with desired options
2. Pipe (|) the output to mlx_lm.generate

**example with `mlx-lm`:**

```bash
llmfiles . --preset markdown --diff \
| python -m mlx_lm.generate --model <your_mlx_model_path_or_name> --temp 0.8
```

**pipe the result directly to `mlx_lm.generate`**
```bash
llmfiles . --include '*.py' --exclude '**/tests/**' \
| python -m mlx_lm.generate --model <your_mlx_model_path_or_name> --max-tokens 1000
```

## example with a different preset and git info:
```bash
llmfiles . --diff --preset default --var project_goal="Refactor API" \
| python -m mlx_lm.generate --model <model_path> --temp 0.7
```
