# llmfiles/core/templating/helpers.py
"""
Custom Handlebars helper functions for llmfiles templates.
"""
import datetime
from typing import Any

def add_helper(*args: Any) -> float:
    """
    Pybars helper to sum numeric arguments. Ignores non-numeric.
    The first argument passed by pybars is the 'this' context, which we ignore.
    """
    # Skip the first argument which is the 'this' context from pybars
    numeric_args = args[1:]
    return sum(float(val) for val in numeric_args if isinstance(val, (int, float)) or (isinstance(val, str) and val.replace('.', '', 1).isdigit()))

def now_utc_iso_helper(*args: Any) -> str:
    """
    Pybars helper to output the current UTC timestamp in ISO 8601 format.
    Ignores arguments.
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

# Dictionary of helpers to be registered with TemplateRenderer
BUILTIN_HELPERS = {
    "add": add_helper,
    "now": now_utc_iso_helper,
    # get_lang_hint is often context-dependent (from util.py) so might be added in __init__.py or renderer.py
}