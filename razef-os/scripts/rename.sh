#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Rename the whole OS.
#
#   ./scripts/rename.sh "Nova OS" nova
#   ./scripts/rename.sh "Axiom"                # slug is derived automatically
#
# Rewrites every text file in the repo (os-release, boot splash, wizard,
# assistant, desktop entries, CI, docs) and renames any file or directory
# whose name contains "razef". Binary files (png, ico, jpg) are left alone.
#
# Commit (or stash) your work first — this edits a lot of files.
# ---------------------------------------------------------------------------
set -euo pipefail

NEW_NAME="${1:-}"
SLUG="${2:-}"

if [[ -z "${NEW_NAME}" ]]; then
  echo "usage: $0 \"New OS Name\" [slug]" >&2
  exit 1
fi

if [[ -z "${SLUG}" ]]; then
  SLUG="$(printf '%s' "${NEW_NAME}" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')"
fi

if [[ ! "${SLUG}" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "error: slug '${SLUG}' must be lowercase alphanumeric with dashes" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

if [[ -n "$(git status -s 2>/dev/null || true)" ]]; then
  echo "warning: working tree is dirty — commit or stash first so you can diff the rename." >&2
  read -r -p "continue anyway? [y/N] " reply
  [[ "${reply}" =~ ^[Yy]$ ]] || exit 1
fi

echo "Renaming to: ${NEW_NAME}  (slug: ${SLUG})"

RENAME_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
export RENAME_SELF

NEW_NAME="${NEW_NAME}" SLUG="${SLUG}" python3 - <<'PY'
import os
import pathlib

new_name = os.environ["NEW_NAME"]
slug = os.environ["SLUG"]
upper = slug.upper()

root = pathlib.Path(".")

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".ico", ".jxl", ".webp", ".gz", ".zip", ".pyc"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "output", ".github/.cache"}

# replacements, applied in this order
rules = [
    ("razef-os", f"{slug}-os"),
    ("razef", slug),
    ("Razef", new_name),
    ("RAZEF_", f"{upper}_"),
    ("RAZEF", upper),
]


# never rewrite the script that is currently running: bash re-reads it from disk
SELF = pathlib.Path(os.environ["RENAME_SELF"]).resolve()


def walk():
    for dirpath, dirnames, filenames in os.walk(root):
        if any(part in SKIP_DIRS for part in pathlib.Path(dirpath).parts):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            path = pathlib.Path(dirpath) / f
            try:
                if path.resolve() == SELF:
                    continue
            except OSError:
                pass
            yield path


changed = 0
for path in walk():
    if path.suffix.lower() in SKIP_SUFFIX:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue

    original = text
    for old, new in rules:
        text = text.replace(old, new)

    if text != original:
        path.write_text(text, encoding="utf-8")
        changed += 1

print(f"  rewrote {changed} files")

# rename files and directories, deepest first
renamed = 0
paths = [
    p
    for p in root.rglob("*")
    if not any(part in SKIP_DIRS for part in p.parts)
    and p.resolve() != SELF
]
for path in sorted(paths, key=lambda p: len(p.parts), reverse=True):
    name = path.name
    new = name
    for old, rep in (("razef-os", f"{slug}-os"), ("razef", slug), ("Razef", new_name)):
        new = new.replace(old, rep)
    if new != name:
        target = path.with_name(new)
        if target.exists():
            print(f"  skip (exists): {target}")
            continue
        path.rename(target)
        renamed += 1

print(f"  renamed {renamed} files/directories")
PY

echo
echo "Next steps:"
echo "  1. Replace the artwork:  branding/master/{logo,wallpaper}.png  then  just brand"
echo "  2. Check the diff:       git diff --stat"
echo "  3. Validate:             just check && just check-python"
echo "  4. Update image-template.env (IMAGE_NAME / REPO_ORGANIZATION) before CI runs."
