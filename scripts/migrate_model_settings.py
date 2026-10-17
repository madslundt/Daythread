"""Move legacy STT/LLM .env defaults into private settings. Stop the backend first."""

import argparse
import io
import os
import tempfile
from pathlib import Path

from dotenv.parser import parse_stream

from omi_local.configuration import Configuration
from omi_local.settings import Settings


def migrate(env_file: Path) -> int:
    original = env_file.read_bytes()
    source = original.decode("utf-8")
    bindings = list(parse_stream(io.StringIO(source)))
    if any(binding.error for binding in bindings):
        raise ValueError("Invalid environment file; no settings were changed")
    removed = {
        binding.key
        for binding in bindings
        if binding.key and binding.key.startswith(("OMI_STT_", "OMI_LLM_"))
    }
    if not removed:
        return 0
    fields = {key.removeprefix("OMI_").lower().split("__")[0] for key in removed}
    if fields - Settings.model_fields.keys():
        raise ValueError("Unknown model environment variable; check the file before migrating")
    kept = [binding for binding in bindings if binding.key not in removed]
    if any(
        f"${{{key}}}" in (binding.value or "") or f"${{{key}:-" in (binding.value or "")
        for binding in kept
        for key in removed
    ):
        raise ValueError("Another environment value references a model variable; update it first")
    initial = Settings(_env_file=env_file)
    configuration = Configuration(initial)
    # Saved UI values and route keys always win over old environment defaults.
    desired = configuration.startup(initial)
    cleaned = ("\ufeff" if source.startswith("\ufeff") else "") + "".join(
        binding.original.string for binding in kept
    )
    descriptor, temporary = tempfile.mkstemp(prefix=".env-models-", dir=env_file.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            output.write(cleaned)
            output.flush()
            os.fsync(output.fileno())
        if env_file.read_bytes() != original:
            raise ValueError(
                "Environment file changed during migration; retry with the backend stopped"
            )
        # Persist first: a failed .env replacement leaves both sources usable.
        configuration.save(desired, fields)
        os.replace(temporary, env_file)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return len(removed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env"
    )
    args = parser.parse_args()
    try:
        count = migrate(args.env_file)
    except OSError, ValueError:
        # Validation exceptions can contain write-only keys; never print their contents.
        parser.exit(1, "Model settings migration failed. Original .env entries were preserved.\n")
    print(
        f"Migrated {count} model environment variables to private settings. "
        "Values are not displayed."
    )


if __name__ == "__main__":
    main()
