"""Backend-specific configuration: auth, CORS, rate-limiter, YAML configs."""

import os
import sys
import secrets
import logging
import yaml
from dotenv import load_dotenv
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("crewai").setLevel(logging.WARNING)


setup_logging()

load_dotenv()

logger = logging.getLogger(__name__)

# ── API key auth ──────────────────────────────────────────────────────────────
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "dev-key-change-in-production")
if INTERNAL_API_KEY == "dev-key-change-in-production":
    logger.warning(
        "⚠️  INTERNAL_API_KEY is using the default value. "
        "Set it in .env before deploying to production!"
    )

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(_API_KEY_HEADER)):
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return api_key


# ── CORS ──────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5500,http://127.0.0.1:5500,http://localhost:3000,https://agentic-support-platform.vercel.app",
).split(",")

# ── JWT ───────────────────────────────────────────────────────────────────────
JWT_SECRET: str = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_HOURS: int = 24

# ── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── YAML agent/task configs ───────────────────────────────────────────────────
with open("config/agents.yaml", "r") as _f:
    agents_config = yaml.safe_load(_f)

with open("config/tasks.yaml", "r") as _f:
    tasks_config = yaml.safe_load(_f)
