"""Ask Gemini what to look into when an alert opens. Sends only aggregate scores and feature names."""

from __future__ import annotations

import json
import os
from typing import Any

import requests

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PROMPT = (
    "A continuous behavioral authentication system watches keyboard and mouse timing on a Windows PC. "
    "It just opened an alert because recent activity no longer matches the enrolled user. You only get "
    "aggregate numbers, never keystrokes, text, or screen content. Suggest 3 to 5 short, concrete things the "
    "account owner should investigate (for example who had access to the PC, new input hardware, remote "
    "sessions, injury or a different posture). Do not claim the user is an intruder. Reply as a JSON array "
    "of strings, each under 140 characters.\n\nAlert context:\n"
)


def investigation_hints(context: dict[str, Any]) -> list[str] | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    response = requests.post(
        URL.format(model=os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")),
        headers={"x-goog-api-key": key},
        json={
            "contents": [{"parts": [{"text": PROMPT + json.dumps(context)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
        },
        timeout=20,
    )
    response.raise_for_status()
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    hints = json.loads(text)
    if not isinstance(hints, list):
        return None
    return [str(item)[:200] for item in hints if isinstance(item, str) and item.strip()][:5] or None
