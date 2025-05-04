# llmfiles/exceptions.py
"""Custom exceptions for the application."""

class SmartPromptBuilderError(Exception):
    """Base exception for this application."""
    pass

class ConfigError(SmartPromptBuilderError):
    """Configuration related errors."""
    pass

class DiscoveryError(SmartPromptBuilderError):
    """File discovery related errors."""
    pass

class ProcessingError(SmartPromptBuilderError):
    """File content processing errors."""
    pass

class GitError(SmartPromptBuilderError):
    """Git command related errors."""
    pass

class TemplateError(SmartPromptBuilderError):
    """Template rendering errors."""
    pass

class OutputError(SmartPromptBuilderError):
    """Output related errors."""
    pass

class TokenizerError(SmartPromptBuilderError):
    """Tokenizer related errors."""
    pass