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
