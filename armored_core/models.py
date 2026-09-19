from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class State(StrEnum):
    RECEIVED = "RECEIVED"
    VISION = "VISION"
    STUDIO = "STUDIO"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    RECOVERY = "RECOVERY"
    FAILED = "FAILED"


class PublicationCheck(StrEnum):
    CONFIRMED = "CONFIRMED"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Item:
    item_id: int
    telegram_message_id: str
    state: State
    workspace: Path
    original_path: Path | None
    working_path: Path | None
    result_path: Path | None
    affiliate_name: str | None
    affiliate_url: str | None
