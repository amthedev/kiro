# -*- coding: utf-8 -*-
"""
Kiro Gateway — Anthropic/OpenAI-compatible proxy backed by HTTP calls to Kiro.

Each Kiro account is a `ksk_` API key. Requests from Claude Code / Claude
Desktop hit this gateway, which exchanges the ksk_ key for an OAuth access token
and calls runtime.kiro.dev directly — no kiro-cli binary needed.

Usage:
    python main.py                  # 0.0.0.0:8000
    python main.py --port 9000
    SERVER_PORT=80 python main.py
"""

import argparse
import logging
import os
import sys
from contextlib import asynccontextmanager

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from kiro.database import init_db
from kiro.routes_admin import router as admin_router
from kiro.routes_proxy import router as proxy_router, close_http_client

DEFAULT_SERVER_HOST = "0.0.0.0"
DEFAULT_SERVER_PORT = 8000
SERVER_HOST = os.getenv("SERVER_HOST", DEFAULT_SERVER_HOST)
SERVER_PORT = int(os.getenv("SERVER_PORT", str(DEFAULT_SERVER_PORT)))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# --- Loguru ---
logger.remove()
logger.add(
    sys.stderr,
    level=LOG_LEVEL,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
    lg = logging.getLogger(name)
    lg.handlers = [InterceptHandler()]
    lg.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Kiro Gateway (HTTP mode)...")
    init_db()
    logger.info("Database initialized (admin.db)")
    logger.info("Using direct HTTP calls to runtime.kiro.dev — no kiro-cli needed")
    logger.info("Admin panel available at /admin")
    yield
    logger.info("Shutting down Kiro Gateway...")
    await close_http_client()


app = FastAPI(
    title="Kiro Gateway",
    description="Anthropic/OpenAI-compatible proxy backed by Kiro HTTP API",
    version="4.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(proxy_router)
app.include_router(admin_router)


UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"default": {"class": "main.InterceptHandler"}},
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
    },
}


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kiro Gateway")
    parser.add_argument("-H", "--host", type=str, default=None)
    parser.add_argument("-p", "--port", type=int, default=None)
    return parser.parse_args()


def print_banner(host: str, port: int) -> None:
    display_host = "localhost" if host == "0.0.0.0" else host
    url = f"http://{display_host}:{port}"
    print()
    print(f"  \033[97m\033[1m👻 Kiro Gateway v4.0.0\033[0m")
    print()
    print(f"  \033[92m\033[1m➜  {url}\033[0m")
    print(f"  \033[2mAdmin panel:   {url}/admin\033[0m")
    print(f"  \033[2mHealth check:  {url}/health\033[0m")
    print(f"  \033[2mMode:          HTTP direct (no kiro-cli)\033[0m")
    print()


if __name__ == "__main__":
    import uvicorn

    args = parse_cli_args()
    host = args.host or SERVER_HOST
    port = args.port or SERVER_PORT

    print_banner(host, port)
    logger.info(f"Starting Uvicorn on {host}:{port}...")

    uvicorn.run("main:app", host=host, port=port, log_config=UVICORN_LOG_CONFIG)
