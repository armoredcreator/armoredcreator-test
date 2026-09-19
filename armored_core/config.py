from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    database: Path
    storage: Path
    logs: Path
    backups: Path
    max_attempts: int = 3
    telegram_bot_token_env: str = "TELEGRAM_BOT_TOKEN"

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        explicit_path = path is not None
        config_path = path or Path(os.getenv("ARMORED_CONFIG", "config/settings.json"))
        config_path = config_path.expanduser().resolve()
        data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        root_value = data.get("root")
        if root_value:
            root = Path(root_value).expanduser().resolve()
        elif explicit_path:
            root = config_path.parent.resolve()
        elif os.getenv("ARMORED_ROOT"):
            root = Path(os.environ["ARMORED_ROOT"]).expanduser().resolve()
        else:
            root = config_path.parent.parent.resolve()

        def under(value: str | None, default: str) -> Path:
            p = Path(value or default).expanduser()
            return p.resolve() if p.is_absolute() else (root / p).resolve()

        return cls(
            root,
            under(data.get("database"), "storage/database"),
            under(data.get("storage"), "storage"),
            under(data.get("logs"), "storage/logs"),
            under(data.get("backups"), "storage/backups"),
            int(data.get("max_attempts", 3)),
            str(data.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN")),
        )

    def ensure(self) -> None:
        for path in (self.storage, self.database, self.logs, self.backups):
            path.mkdir(parents=True, exist_ok=True)
