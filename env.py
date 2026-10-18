import os
from pathlib import Path


def _load_dotenv(path=".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

MONOBANK_TOKEN = os.environ.get("MONOBANK_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_PASSWORD = os.environ.get("BOT_PASSWORD", "")

RATE_URL = "https://api.monobank.ua/bank/currency"
PERSONAL_URL = "https://api.monobank.ua/personal/client-info"
STATEMENTS_URL = "https://api.monobank.ua/personal/statement/{account}/{from_ts}/{to_ts}"
WEBHOOK_URL = "https://api.monobank.ua/personal/webhook"

WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8443"))
WEBHOOK_PUBLIC_URL = os.environ.get("WEBHOOK_PUBLIC_URL", "")

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "65"))
