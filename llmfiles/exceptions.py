class SmartPromptBuilderError(Exception):
    # base exception for all application-specific errors.
    pass

class ConfigError(SmartPromptBuilderError):
    # errors related to configuration.
    pass

class DiscoveryError(SmartPromptBuilderError):
    # errors during file discovery.
    pass

class TemplateError(SmartPromptBuilderError):
    # errors related to template rendering.
    pass

class OutputError(SmartPromptBuilderError):
    # errors during output operations.
    pass

class TokenizerError(SmartPromptBuilderError):
    # errors from the tokenizer.
    pass

class GitError(SmartPromptBuilderError):
    # errors from git commands.
    pass
