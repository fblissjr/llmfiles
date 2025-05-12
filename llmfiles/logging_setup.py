# llmfiles/logging_setup.py
import logging  # standard library logging
import sys
import structlog # for structured logging

# these processors are applied sequentially to log entries.
# order matters.
SHARED_LOG_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.ExtraAdder(),
    structlog.processors.StackInfoRenderer(),
    structlog.dev.set_exc_info,
    structlog.processors.format_exc_info,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]

def configure_logging(log_level_str: str = "warning", force_json_logs: bool = False):
    """
    configures structlog for the application.

    args:
        log_level_str: the desired logging level as a string (e.g., "info", "debug").
        force_json_logs: if true, forces json output for logs, otherwise uses console-friendly.
    """
    log_level_int = getattr(logging, log_level_str.upper(), logging.WARNING)

    final_structlog_processor: structlog.types.Processor
    # define the foreign_pre_chain. these processors are applied to logs
    # coming from the standard library logging system before they hit the
    # final_structlog_processor.
    # this ensures that stdlib logs also get structlog's enrichments.
    stdlib_log_enrichment_processors = (
        SHARED_LOG_PROCESSORS
        + [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,  # cleans up internal structlog data
        ]
    )

    if force_json_logs or not sys.stderr.isatty():  # use json if forced or not a tty
        final_structlog_processor = structlog.processors.JSONRenderer()
    else:  # console-friendly output
        final_structlog_processor = structlog.dev.ConsoleRenderer(
            colors=True, exception_formatter=structlog.dev.plain_traceback
        )

    # configure structlog itself.
    # processors here are for logs created by `structlog.get_logger()`.
    # `wrap_for_formatter` prepares it for a standard library handler.
    structlog.configure(
        processors=SHARED_LOG_PROCESSORS
        + [  # applies to structlog-native logs
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
        # `processor` here is the default final processor for records from `foreign_pre_chain`
        # if they don't get handled by a specific handler's formatter.
        # however, we will set a specific formatter on our handler.
    )

    # create the stdlib handler's formatter using `ProcessorFormatter`.
    # this formatter will use the `final_structlog_processor` (json or console) for rendering
    # both structlog-native logs (after `wrap_for_formatter`) and stdlib logs (after `foreign_pre_chain`).
    stdlib_handler_formatter = structlog.stdlib.ProcessorFormatter(
        # the `processor` argument here is the one that does the actual final rendering.
        processor=final_structlog_processor,
        # `foreign_pre_chain` processes log records from non-structlog loggers
        # (i.e., standard library loggers) before they hit the main `processor`.
        foreign_pre_chain=stdlib_log_enrichment_processors,  # CORRECTLY USED HERE
    )

    # configure the root logger for the 'llmfiles' namespace.
    app_root_logger = logging.getLogger("llmfiles")
    
    # clear any existing handlers to prevent duplicate logs if reconfigured.
    if app_root_logger.hasHandlers():
        app_root_logger.handlers.clear()

    # add a standard library handler, now using the structlog-aware formatter.
    handler = logging.StreamHandler(sys.stderr)  # log to stderr.
    handler.setFormatter(
        stdlib_handler_formatter
    )  # use the structlog-configured stdlib formatter.

    app_root_logger.addHandler(handler)
    app_root_logger.setLevel(
        log_level_int
    )  # set the effective log level for our app's namespace.

    # optionally, configure the *root* python logger to also use this formatter
    # if you want all logs from all libraries to be processed by structlog.
    # this can be noisy but useful for debugging library issues.
    # python_root_logger = logging.getLogger()
    # if not any(isinstance(h.formatter, structlog.stdlib.ProcessorFormatter) for h in python_root_logger.handlers):
    #     # only add if no structlog formatter already on root to avoid duplicates
    #     root_handler = logging.StreamHandler(sys.stderr)
    #     root_handler.setFormatter(stdlib_handler_formatter)
    #     python_root_logger.addHandler(root_handler)
    #     python_root_logger.setLevel(log_level_int) # or a different level for external libs

    slog = structlog.get_logger(
        "llmfiles.logging_setup"
    )  # get a structlog logger for this module.
    slog.info(  # use the structlog logger for this confirmation.
        "logging configured.",
        log_level=log_level_str,
        output_type="json" if force_json_logs or not sys.stderr.isatty() else "console",
    )