"""
=========================================================
ArmoredStudio
Arquivo: logger.py
DescriÃ§Ã£o: Sistema central de logs.
=========================================================
"""

import logging
from pathlib import Path

from .config import LOGS_DIR


# =========================================================
# CONFIGURAÃ‡ÃƒO DO LOGGER
# =========================================================

LOG_FILE = LOGS_DIR / "sistema.log"


def criar_logger():

    LOGS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    logger = logging.getLogger("ArmoredStudio")

    logger.setLevel(logging.DEBUG)

    # Evita criar mÃºltiplos handlers
    if logger.handlers:
        return logger


    # -----------------------------------------------------
    # Arquivo de log
    # -----------------------------------------------------

    arquivo_handler = logging.FileHandler(
        LOG_FILE,
        encoding="utf-8"
    )

    arquivo_handler.setLevel(
        logging.DEBUG
    )


    # -----------------------------------------------------
    # Console
    # -----------------------------------------------------

    console_handler = logging.StreamHandler()

    console_handler.setLevel(
        logging.INFO
    )


    # -----------------------------------------------------
    # Formato
    # -----------------------------------------------------

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )


    arquivo_handler.setFormatter(
        formato
    )

    console_handler.setFormatter(
        formato
    )


    logger.addHandler(
        arquivo_handler
    )

    logger.addHandler(
        console_handler
    )


    return logger


# =========================================================
# LOGGER GLOBAL
# =========================================================

log = criar_logger()



