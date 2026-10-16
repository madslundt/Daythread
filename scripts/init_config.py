"""Create private local configuration once, without printing credentials."""

import os
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    target = root / ".env"
    example = (root / "config" / "omi.env.example").read_text(encoding="utf-8")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Existing .env preserved.")
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as config:
        config.write(example)
    print("Created .env. No application authentication is enabled. Review local model endpoints.")


if __name__ == "__main__":
    main()
