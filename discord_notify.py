"""
Discord Webhook にメッセージを投稿する。
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


def post(content: str) -> None:
    if not WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL が設定されていません。.env を確認してください。")

    resp = requests.post(
        WEBHOOK_URL,
        json={"content": content},
        timeout=10,
    )
    resp.raise_for_status()
