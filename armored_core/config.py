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
        config_path = path or Path(os.getenv("ARMORED_CONFIG", "config/settings.json"))
        if not config_path.is_absolute():
            root = Path(os.getenv("ARMORED_ROOT", Path.cwd())).expanduser().resolve()
            config_path = root / config_path
        config_path = config_path.resolve()
        data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        root = Path(os.getenv("ARMORED_ROOT", data.get("root", config_path.parent.parent))).expanduser().resolve()
        storage = root / data.get("storage", "storage")
        database = root / data.get("database", "storage/database")
        logs = root / data.get("logs", "storage/logs")
        backups = root / data.get("backups", "storage/backups")
        return cls(root, database, storage, logs, backups, int(data.get("max_attempts", 3)), str(data.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN")))

    def ensure(self) -> None:
        for path in (self.storage, self.database, self.logs, self.backups):
            path.mkdir(parents=True, exist_ok=True)
