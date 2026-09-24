"""Authorize only this project's isolated Dreamina home; never submit media."""

from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from app.media import MediaEngine

    engine = MediaEngine(root)
    if not engine.available():
        print("Run scripts/install.ps1 first; the Dreamina CLI is missing.", file=sys.stderr)
        return 1
    # Share the adapter's isolated environment so login and generation agree.
    # It also discards inherited DREAMINA_/JIMENG_ variables from other projects.
    env = engine.cli_environment()
    print("This login is isolated to .local/dreamina-home; no old credentials are copied.")
    print("Complete authorization yourself on the official page printed below.")
    print("This command does not submit images or videos. Do not share the device code.")
    return subprocess.call([str(engine.cli), "login"], cwd=root, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
