# llmfiles/core/templating/renderer.py
"""
Contains the TemplateRenderer class responsible for loading, compiling,
and rendering Handlebars templates.
"""
from pathlib import Path
from typing import Dict, Any
import pybars # type: ignore
import structlog

from llmfiles.config.settings import PromptConfig, OutputFormat, PresetTemplate
from llmfiles.exceptions import TemplateError
from llmfiles.util import get_language_hint # For the get_lang_hint helper

# Import default template strings
from .default_templates import (
    DEFAULT_MARKDOWN_TEMPLATE, 
    DEFAULT_XML_TEMPLATE, 
    PRESET_CLAUDE_OPTIMAL_TEMPLATE
)
# Import built-in helpers
from .helpers import BUILTIN_HELPERS 

log = structlog.get_logger(__name__)

class TemplateRenderer:
    """Manages loading, compilation, and rendering of Handlebars templates."""
    def __init__(self, config: PromptConfig):
        self.config = config
        self.handlebars_compiler = pybars.Compiler()
        
        # Combine built-in helpers with any others, like get_lang_hint
        self.registered_helpers: Dict[str, callable] = {
            **BUILTIN_HELPERS, # from helpers.py
            "get_lang_hint": lambda _this, ext_str: get_language_hint(ext_str or ""),
        }
        
        self.template_source_name: str = "unknown_source"
        self.raw_template_string: str = self._determine_and_load_template_string()

        try: 
            self.compiled_template_function = self.handlebars_compiler.compile(self.raw_template_string)
            log.debug("template_compiled_successfully", source=self.template_source_name)
        except Exception as e:
            log.error("template_compilation_failed", source=self.template_source_name, error=str(e), exc_info=True)
            raise TemplateError(f"Failed to compile template from '{self.template_source_name}': {e}") from e

    def _determine_and_load_template_string(self) -> str:
        """Loads template string based on config: custom file > preset > default based on output_format."""
        if self.config.template_path: 
            self.template_source_name = f"custom_file:{self.config.template_path}"
            log.info("loading_custom_template_from_path", path=str(self.config.template_path))
            try:
                return self.config.template_path.read_text(encoding="utf-8")
            except Exception as e:
                raise TemplateError(f"Failed to read custom template file {self.config.template_path}: {e}") from e

        if self.config.preset_template: 
            self.template_source_name = f"preset:{self.config.preset_template.value}"
            log.info("using_preset_template_by_name", name=self.config.preset_template.value)
            preset_map = {
                PresetTemplate.DEFAULT: DEFAULT_MARKDOWN_TEMPLATE,
                PresetTemplate.CLAUDE_OPTIMAL: PRESET_CLAUDE_OPTIMAL_TEMPLATE,
                PresetTemplate.GENERIC_XML: DEFAULT_XML_TEMPLATE,
            }
            if self.config.preset_template in preset_map:
                return preset_map[self.config.preset_template]
            log.warning("unknown_preset_template_name_specified_falling_back", preset_name=self.config.preset_template.value)

        self.template_source_name = f"default_for_output_format:{self.config.output_format.value}"
        log.info("using_default_template_for_output_format", format=self.config.output_format.value)
        if self.config.output_format == OutputFormat.XML: return DEFAULT_XML_TEMPLATE
        if self.config.output_format == OutputFormat.JSON: return "{{json_content_for_prompt_field}}" 
        return DEFAULT_MARKDOWN_TEMPLATE

    def render(self, template_context_data: Dict[str, Any]) -> str:
        """Renders the compiled template with the given context data."""
        log.info("rendering_template_with_context", source=self.template_source_name, 
                   context_keys=list(template_context_data.keys()))
        try:
            rendered_string = self.compiled_template_function(
                template_context_data, helpers=self.registered_helpers
            )
            log.debug("template_rendered_successfully", source=self.template_source_name)
            return rendered_string.strip() + "\n" # Ensure single trailing newline
        except Exception as e: 
            log.error("template_rendering_error_occurred", source=self.template_source_name, 
                      error_message=str(e), exc_info=True)
            # More specific error for missing variables often helps debugging templates
            if isinstance(e, pybars.PybarsError) and ("missing" in str(e).lower() or isinstance(e.original_exception, NameError)): 
                raise TemplateError(
                    f"Template render failed for '{self.template_source_name}': A required variable might be missing, "
                    f"or there was an access error. Pybars detail: {e}") from e
            raise TemplateError(f"Template render failed for '{self.template_source_name}': {e}") from e