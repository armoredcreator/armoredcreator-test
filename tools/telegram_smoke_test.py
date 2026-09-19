from __future__ import annotations

import argparse
import sys

from armored_core.telegram_publisher import TelegramPublisher


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram check-only smoke test.")
    parser.add_argument("--publish", action="store_true", help="reserved for an explicit real publication test")
    args = parser.parse_args()

    if args.publish:
        print("REAL PUBLICATION IS INTENTIONALLY NOT AUTOMATED BY THIS SMOKE TEST.")
        print("Use the TelegramPublisher from an explicitly configured runtime.")
        return 2

    try:
        publisher = TelegramPublisher()
        result = publisher.check_publication
    except RuntimeError as exc:
        print(f"CONFIG/DEPENDENCY ERROR: {exc}")
        return 2

    print("Telegram publisher loaded successfully.")
    print("check_publication is available; no message was published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
