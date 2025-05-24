# llmfiles/core/templating/__init__.py
"""
Templating module for llmfiles.

Provides access to the TemplateRenderer for compiling and rendering prompts,
and the build_template_context function for preparing data for templates.
"""
from .renderer import TemplateRenderer
from .context_builder import build_template_context
# Default templates and helpers are used internally by renderer/context_builder,
# no need to export them directly unless desired for advanced extension.

__all__ = [
    "TemplateRenderer",
    "build_template_context"
]