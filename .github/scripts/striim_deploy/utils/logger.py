"""Centralized logging configuration"""

import logging
import os
from typing import Optional, Dict


# Dictionary to store configured loggers
_configured_loggers: Dict[str, logging.Logger] = {}


def configure_logging(
    name: str, settings=None, level: Optional[str] = None
) -> logging.Logger:
    """
    Configure logging with settings.

    Args:
        name: Logger name
        settings: SettingsModel instance (optional)
        level: Explicit log level (overrides settings if provided)

    Returns:
        Configured logger
    """
    # Check if we've already configured this logger
    if name in _configured_loggers:
        return _configured_loggers[name]

    logger = logging.getLogger(name)

    # Clear existing handlers to avoid duplication
    if logger.handlers:
        logger.handlers = []

    # Determine log level
    log_level_name = level

    if not log_level_name and settings:
        logging_config = settings.get("logging", {})
        log_level_name = logging_config.get("level", "info")

    if not log_level_name:
        log_level_name = "info"

    # Convert string level to logging constant
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)
    logger.setLevel(log_level)

    # Create console handler
    handler = logging.StreamHandler()

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Set log file if specified in environment
    log_file = os.getenv("STRIIM_LOG_FILE")
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Disable propagation to avoid duplicate logs
    logger.propagate = False

    # Store the configured logger in our registry
    _configured_loggers[name] = logger

    # If settings is a SettingsModel instance, set its logger
    if hasattr(settings, "set_logger"):
        settings.set_logger(logger)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a previously configured logger or create a new one.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    if name in _configured_loggers:
        return _configured_loggers[name]
    return configure_logging(name)
