# llmfiles/exceptions.py
"""Custom exceptions for the llmfiles application."""

class SmartPromptBuilderError(Exception):
    """Base exception for all llmfiles application errors."""
    pass

class ConfigError(SmartPromptBuilderError):
    """Errors related to configuration validation or loading."""
    pass

class DiscoveryError(SmartPromptBuilderError):
    """Errors during file and directory discovery."""
    pass

class GitError(SmartPromptBuilderError):
    """Errors from Git command execution or repository issues."""
    pass

class TemplateError(SmartPromptBuilderError):
    """Errors in template loading, compilation, or rendering."""
    pass

class OutputError(SmartPromptBuilderError):
    """Errors during output operations (e.g., writing to file/clipboard)."""
    pass

class TokenizerError(SmartPromptBuilderError):
    """Errors related to token counting or tokenizer interactions."""
    pass