from __future__ import annotations

import argparse
import sys
from pathlib import Path

from armored_core.models import Item, State
from armored_core.telegram_publisher import TelegramPublisher


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram check-only smoke test.")
    parser.add_argument("--item-id", type=int, required=True)
    parser.add_argument("--publish", action="store_true", help="refuse: publication requires an explicit application flow")
    args = parser.parse_args()
    if args.publish:
        print("REFUSED: smoke test is check-only; no Telegram message will be sent.")
        return 2

    try:
        publisher = TelegramPublisher()
        item = Item(
            item_id=args.item_id,
            telegram_message_id="smoke-test",
            state=State.PUBLISHING,
            workspace=Path("."),
            original_path=None,
            working_path=None,
            result_path=None,
            affiliate_name=None,
            affiliate_url=None,
        )
        result = publisher.check_publication(item)
    except RuntimeError as exc:
        print(f"CONFIG/DEPENDENCY ERROR: {exc}")
        return 2

    print(f"telegram_publication_check={result.value}")
    return 0 if result.value in {"CONFIRMED", "ABSENT"} else 3


if __name__ == "__main__":
    sys.exit(main())
