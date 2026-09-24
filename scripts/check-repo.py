"""Offline release hygiene checks; never reads ignored local credentials."""

from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {
    ".local", ".tools", "data", "projects", "media-tasks", "outputs", "uploads",
    "models", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".git", ".idea", ".vscode",
}
PRIVATE_SUFFIXES = {
    ".key", ".pem", ".p12", ".pfx", ".log", ".sqlite", ".db", ".exe", ".dll",
    ".mp4", ".mov", ".mp3", ".wav", ".mkv", ".pt", ".pth", ".safetensors",
    ".ckpt", ".onnx", ".zip", ".pyc",
}
TEXT_SUFFIXES = {".md", ".py", ".js", ".cjs", ".html", ".css", ".ps1", ".cmd", ".yml", ".yaml", ".json", ".txt"}
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
]


def candidates() -> list[Path]:
    # Never let a staging directory accidentally inherit its parent's Git index.
    git = shutil.which("git")
    if (ROOT / ".git").exists() and git:
        result = subprocess.run([git, "-C", str(ROOT), "ls-files", "-z"], check=True, capture_output=True)
        return [ROOT / path.decode("utf-8") for path in result.stdout.split(b"\0") if path]
    return [path for path in ROOT.rglob("*") if path.is_file()
            and not any(part in PRIVATE_DIRS for part in path.relative_to(ROOT).parts)]


def main() -> int:
    issues: list[str] = []
    paths = candidates()
    for path in paths:
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if any(part in PRIVATE_DIRS for part in parts):
            issues.append(f"Private/runtime path tracked: {rel}")
            continue  # Do not open a credential even if it was accidentally tracked.
        lower_name = path.name.lower()
        if path.suffix.lower() in PRIVATE_SUFFIXES or lower_name.startswith(".env") or "credentials" in lower_name or "secrets" in lower_name:
            issues.append(f"Sensitive/generated file tracked: {rel}")
            continue
        if not path.is_file():
            issues.append(f"Tracked file missing: {rel}")
            continue
        if path.stat().st_size > 5 * 1024 * 1024:
            issues.append(f"File exceeds 5 MiB source/screenshot budget: {rel}")
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != "LICENSE":
            continue
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            issues.append(f"Text must be UTF-8: {rel}")
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                issues.append(f"Possible credential/private key in {rel} (value not printed)")
        if path.suffix.lower() == ".md":
            for target in re.findall(r"!?\[[^\]\n]*\]\(([^)\n]+)\)", content):
                target = target.strip().split(' "', 1)[0].strip("<>")
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                linked = (path.parent / unquote(parsed.path)).resolve()
                if not linked.is_relative_to(ROOT):
                    issues.append(f"Documentation link escapes repository in {rel}")
                elif not linked.exists():
                    issues.append(f"Broken local documentation link in {rel}: {parsed.path}")
    license_path = ROOT / "LICENSE"
    if not license_path.exists() or "Copyright (c) 2026 Howie" not in license_path.read_text(encoding="utf-8"):
        issues.append("Original Howie copyright notice missing from LICENSE")
    if issues:
        print("Repository hygiene failed:")
        for issue in issues:
            print("- " + issue)
        return 1
    print(f"Repository hygiene passed ({len(paths)} source/documentation files checked).")
    print("Automated checks do not replace a manual secret and screenshot review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
