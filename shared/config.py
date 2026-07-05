import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Load THIS project's .env regardless of cwd (real env vars still win).
load_dotenv(PROJECT_ROOT / ".env")
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "couples.db"
DATABASE_URL = os.getenv("DATABASE_URL", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# LLM sorter (bot service only)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SORTER_MODEL = os.getenv("SORTER_MODEL", "claude-opus-4-8")

# Web app door (web service only)
APP_PASSCODE = os.getenv("APP_PASSCODE", "")
APP_SECRET = os.getenv("APP_SECRET", "")

# Household defaults — used until setup is completed, then settings win
DEFAULT_CURRENCY_CODE = "CZK"
DEFAULT_CURRENCY_SYMBOL = "Kč"
DEFAULT_TIMEZONE = "Europe/Prague"

APP_NAME = "Together"
