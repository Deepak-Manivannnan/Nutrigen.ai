import logging
import sys
from backend.app.config import settings

def setup_logging():
    level = logging.DEBUG if settings.debug else logging.INFO
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Avoid duplicate handlers if re-initialized
    if not root_logger.handlers:
        root_logger.addHandler(handler)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
