# llmfiles/core/discovery/__init__.py
"""
Path discovery and filtering module for llmfiles.

This package handles finding relevant file paths based on user inputs,
configuration, .gitignore rules, and include/exclude patterns.
"""
# Re-export the main discovery function for easier access
from .walker import discover_paths

# You can also re-export other key functions or classes if they are meant
# to be part of the public API of this discovery sub-package.
# For now, just discover_paths seems sufficient.

__all__ = ["discover_paths"]