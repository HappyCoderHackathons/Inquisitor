from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict


class PrivacyPreferences(TypedDict):
    keyboard_collection: bool
    mouse_collection: bool
    model_training: bool


class User(TypedDict):
    _id: str
    email: str
    created_at: datetime
    privacy_preferences: PrivacyPreferences


ConsentScope = Literal["keyboard_collection", "mouse_collection", "model_training"]


class ConsentRecord(TypedDict):
    _id: str
    user_id: str
    scope: ConsentScope
    granted_at: datetime
    revoked_at: datetime | None


DEFAULT_PRIVACY_PREFERENCES: PrivacyPreferences = {
    "keyboard_collection": False,
    "mouse_collection": False,
    "model_training": False,
}
