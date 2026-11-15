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


DevicePairingState = Literal["paired", "revoked"]


class Device(TypedDict):
    _id: str
    name: str
    user_id: str
    pairing_state: DevicePairingState
    token_hash: str
    created_at: datetime
    last_heartbeat_at: datetime | None
    revoked_at: datetime | None


EnrollmentSessionStatus = Literal["active", "completed", "cancelled"]

class EnrollmentSession(TypedDict):
    _id: str
    onboarding_id: str
    device_id: str
    game: str
    started_at: datetime
    ended_at: datetime | None
    status: EnrollmentSessionStatus
    valid_window_count: int
    credited_ms: int
    generation: int


DEFAULT_PRIVACY_PREFERENCES: PrivacyPreferences = {
    "keyboard_collection": False,
    "mouse_collection": False,
    "model_training": False,
}
