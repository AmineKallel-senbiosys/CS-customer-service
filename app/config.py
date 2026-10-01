from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

KB_PATH = ROOT / "KB" / "velio_kb_v1.yaml"
BATTERY_CSV_PATH = ROOT / "KB" / "battery_runtime_by_user.csv"
WEB_DIR = ROOT / "web"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system.txt"
EMBEDDING_CACHE = ROOT / ".cache" / "article_embeddings.json"


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


OPENAI_API_KEY = env("OPEN_AI_API_KEY") or env("OPENAI_API_KEY")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_EMBEDDING_MODEL = env("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_TEMPERATURE = float(env("OPENAI_TEMPERATURE", "0.2") or "0.2")

HUBSPOT_TOKEN = env("HUBSPOT_PRIVATE_APP_TOKEN")
HUBSPOT_BASE_URL = env("HUBSPOT_BASE_URL", "https://api.hubapi.com").rstrip("/")
HUBSPOT_PORTAL_ID = env("HUBSPOT_PORTAL_ID")

FLOSHIP_TOKEN = env("Floship_API_Token") or env("FLOSHIP_API_TOKEN")
FLOSHIP_BASE_URL = "https://admin.floship.com/api/v2"

SHOPIFY_TOKEN = env("SHOPIFY_API_KEY")
SHOPIFY_SHOP = env("SHOPIFY_SHOP")

# Same demo code as the current Velio chatbot until a mail provider is configured.
OTP_CODE = env("OTP_CODE", "000111")
HOST = env("VELIO_HOST", "127.0.0.1")
PORT = int(env("VELIO_PORT", "8000") or "8000")
