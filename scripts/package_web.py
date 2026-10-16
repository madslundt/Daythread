"""Put the built local web client into the Python package for one-server deployment."""

import shutil
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    source = root / "src" / "web" / "dist"
    target = root / "src" / "omi_local" / "web"
    if not (source / "index.html").is_file():
        raise SystemExit("Build the client first: npm --prefix src/web run build")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    print("Packaged web client into the local backend.")


if __name__ == "__main__":
    main()
