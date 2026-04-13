import argparse
import asyncio
import os

import httpx
from dotenv import load_dotenv

load_dotenv()


async def set_webhook(base_url: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required in .env")
    if not webhook_secret:
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET is required in .env")

    endpoint = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    webhook_url = f"{base_url.rstrip('/')}/webhook/telegram"
    payload = {
        "url": webhook_url,
        "secret_token": webhook_secret,
        "allowed_updates": ["message"],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(endpoint, json=payload)
        response.raise_for_status()
        data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram webhook setup failed: {data}")
    print(f"Webhook set successfully: {webhook_url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure Telegram webhook URL.")
    parser.add_argument("--url", required=True, help="Public base URL for backend")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(set_webhook(args.url))
