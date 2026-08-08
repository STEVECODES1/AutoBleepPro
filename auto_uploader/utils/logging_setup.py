"""Sets up separate youtube.log / rumble.log files plus console output."""

import logging
import os


def setup_logger(name: str, logs_folder: str) -> logging.Logger:
    os.makedirs(logs_folder, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured (e.g. re-imported)

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(os.path.join(logs_folder, f"{name}.log"), encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger


def setup_publisher_logging(logs_folder: str) -> logging.Logger:
    """Make the publishers' own errors visible and keep them on disk.

    publishers/*.py report through logging ("Instagram: container error:
    ..."), and nothing configured those loggers - so the reason a post
    failed went nowhere. All that survived was the guard's count, and
    "circuit breaker open: 3 consecutive failures" three uploads later
    with no way to find out what the failures were.

    One handler pair on the shared "publisher" parent covers every
    publisher, including ones added later.
    """
    os.makedirs(logs_folder, exist_ok=True)
    logger = logging.getLogger("publisher")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(
        os.path.join(logs_folder, "publishers.log"), encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    # Only failures on the console. The successes are already announced
    # by the [Social] lines, and duplicating them would bury the one line
    # that matters.
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("[Publisher] %(message)s"))
    logger.addHandler(console)
    return logger
