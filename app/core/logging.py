"""
Configuração de logging centralizada para o ARGUS.

Substitui prints de DEBUG por logging estruturado, permitindo
controle de nível via variável de ambiente LOG_LEVEL.
"""

import logging
import os
import sys


def setup_logging() -> None:
    """Configura o logging global da aplicação."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)

    # Evita duplicar handlers em reloads (uvicorn --reload)
    if not root.handlers:
        root.addHandler(handler)

    # Reduz verbosidade de bibliotecas externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Retorna um logger nomeado para o módulo."""
    return logging.getLogger(name)
