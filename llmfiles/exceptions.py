# llmfiles/exceptions.py
"""custom exceptions for the llmfiles application."""

class SmartPromptBuilderError(Exception):
    """base exception for all llmfiles application errors."""
    pass

class ConfigError(SmartPromptBuilderError):
    """errors related to configuration validation or loading."""
    pass

class DiscoveryError(SmartPromptBuilderError):
    """errors during file and directory discovery."""
    pass

class GitError(SmartPromptBuilderError):
    """errors from git command execution or repository issues."""
    pass

class TemplateError(SmartPromptBuilderError):
    """errors in template loading, compilation, or rendering."""
    pass

class OutputError(SmartPromptBuilderError):
    """errors during output operations (e.g., writing to file/clipboard)."""
    pass

class TokenizerError(SmartPromptBuilderError):
    """errors related to token counting or tokenizer interactions."""
    pass