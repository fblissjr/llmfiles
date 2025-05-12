# llmfiles/logging_setup.py
"""centralized structlog configuration for the llmfiles application."""
import logging
import sys
import structlog # for structured logging

# these processors are applied sequentially to log entries.
# order matters.
SHARED_LOG_PROCESSORS = [
    structlog.contextvars.merge_contextvars, # allows context-local logging variables
    structlog.stdlib.add_logger_name,        # adds the name of the logger (e.g., 'llmfiles.cli')
    structlog.stdlib.add_log_level,          # adds the log level (e.g., 'info', 'error')
    structlog.stdlib.ExtraAdder(),           # allows adding extra fixed fields if needed
    structlog.processors.stack_info_renderer,  # renders stack info for exceptions
    structlog.dev.set_exc_info,              # ensures exception info is captured correctly
    structlog.processors.format_exc_info,    # formats exception info into a string
    structlog.processors.time_stamper(fmt="iso", utc=True), # adds utc iso timestamps
]

def configure_logging(log_level_str: str = "warning", force_json_logs: bool = False):
    """
    configures structlog for console-friendly or json output.

    args:
        log_level_str: desired logging level ("debug", "info", "warning", "error").
        force_json_logs: if true, output logs in json format, regardless of tty.
    """
    log_level_int = getattr(logging, log_level_str.upper(), logging.WARNING)

    # choose renderer based on whether output is to a tty or if json is forced.
    # json logs are better for machine parsing (e.g., in production or ci).
    # console logs are better for human readability during development.
    if force_json_logs or not sys.stderr.isatty():
        # for non-interactive environments or when json is forced.
        final_processors = SHARED_LOG_PROCESSORS + [
            structlog.processors.dict_tracebacks, # makes tracebacks json-friendly
            structlog.processors.json_renderer(), # renders the log entry as a json string
        ]
        formatter = structlog.stdlib.ProcessorFormatter.wrap_for_formatter(
            # this processor prepares the log entry for the standard library handler.
            # it's the last step before the standard library handler formats it (if it does).
            # for jsonrenderer, the handler's formatter is usually ignored or minimal.
            processor=structlog.processors.json_renderer(),
            logger_factory=structlog.stdlib.LoggerFactory(), # use standard library loggers
        )
    else:
        # for interactive console sessions (tty).
        final_processors = SHARED_LOG_PROCESSORS + [
            structlog.dev.console_renderer(colors=True), # pretty, colored output.
        ]
        formatter = structlog.stdlib.ProcessorFormatter.wrap_for_formatter(
            # console_renderer does its own formatting, so the stdlib formatter is mostly a pass-through.
            processor=structlog.dev.console_renderer(colors=True),
            logger_factory=structlog.stdlib.LoggerFactory(),
        )

    # configure structlog itself.
    structlog.configure(
        processors=final_processors,
        logger_factory=structlog.stdlib.LoggerFactory(), # produces standard library loggers
        wrapper_class=structlog.stdlib.BoundLogger,      # the logger instances returned by structlog.get_logger()
        cache_logger_on_first_use=True,                  # optimization
    )

    # configure the root logger for the 'llmfiles' namespace.
    # this ensures all loggers created under 'llmfiles' (e.g., llmfiles.cli, llmfiles.discovery)
    # will inherit this configuration and level.
    app_root_logger = logging.getLogger("llmfiles")
    
    # clear any existing handlers to prevent duplicate logs if reconfigured.
    if app_root_logger.hasHandlers():
        app_root_logger.handlers.clear()

    # add a standard library handler. structlog's processors will format the log record
    # before it reaches this handler's formatter.
    handler = logging.StreamHandler(sys.stderr) # log to stderr.
    # the formatter on the handler is less critical when using structlog's stdlib.processorformatter,
    # as structlog's processors do most of the work.
    # a simple pass-through formatter or none might be used.
    # however, `processorformatter.wrap_for_formatter` is designed to work with stdlib formatters.
    handler.setFormatter(formatter) # use the structlog-wrapped formatter.
    
    app_root_logger.addHandler(handler)
    app_root_logger.setLevel(log_level_int) # set the effective log level.

    # disable propagation if you only want this specific logger to handle 'llmfiles' messages
    # and not pass them up to the root logger of the python logging hierarchy.
    # app_root_logger.propagate = False 

    log = structlog.get_logger("llmfiles.logging_setup") # get a structlog logger for this module.
    log.info(
        "logging configured.",
        log_level=log_level_str,
        output_type="json" if force_json_logs or not sys.stderr.isatty() else "console"
    )