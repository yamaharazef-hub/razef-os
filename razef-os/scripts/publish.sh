#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Publish Razef OS to your own GitHub repo so Actions can build the image.
#
#   ./scripts/publish.sh              # interactive
#   ./scripts/publish.sh razef-os     # repo name
#
# Needs: git, and the GitHub CLI (gh) logged in  ->  gh auth login
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_NAME="${1:-razef-os}"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---- prerequisites ---------------------------------------------------------
command -v git >/dev/null || die "git is not installed"
command -v gh  >/dev/null || die "install the GitHub CLI first: https://cli.github.com"
gh auth status >/dev/null 2>&1 || die "run 'gh auth login' first"

# ---- identity --------------------------------------------------------------
OWNER="$(gh api user --jq .login)"
SLUG="$(printf '%s' "${OWNER}" | tr '[:upper:]' '[:lower:]')"
say "GitHub user: ${OWNER}"

# keep image-template.env in sync with the actual account/repo
if command -v sed >/dev/null; then
  sed -i.bak \
    -e "s|^IMAGE_NAME=.*|IMAGE_NAME=\"${REPO_NAME}\"|" \
    -e "s|^REPO_ORGANIZATION=.*|REPO_ORGANIZATION=\"${OWNER}\"|" \
    -e "s|^IMAGE_LOGO_URL=.*|IMAGE_LOGO_URL=\"https://raw.githubusercontent.com/${OWNER}/${REPO_NAME}/main/branding/assets/logo-512.png\"|" \
    image-template.env
  rm -f image-template.env.bak
fi
# point the docs at the real image reference
grep -rl 'ghcr.io/<your-username>' docs README.md 2>/dev/null | while read -r f; do
  sed -i.bak "s|ghcr.io/<your-username>|ghcr.io/${SLUG}|g" "$f" && rm -f "${f}.bak"
done

# ---- git -------------------------------------------------------------------
if [[ ! -d .git ]]; then
  say "initialising git repository"
  git init -q
  git add -A
  git commit -q -m "Razef OS: branded Bazzite with a local AI assistant"
else
  say "git repository already exists"
  git add -A
  git diff --cached --quiet || git commit -q -m "Update Razef OS"
fi

# ---- create + push ---------------------------------------------------------
if gh repo view "${OWNER}/${REPO_NAME}" >/dev/null 2>&1; then
  say "repo ${OWNER}/${REPO_NAME} already exists — pushing to it"
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://github.com/${OWNER}/${REPO_NAME}.git"
  git branch -M main
  git push -u origin main
else
  say "creating public repo ${OWNER}/${REPO_NAME}"
  # public = unlimited Actions minutes. A private repo burns ~50 min per build.
  gh repo create "${REPO_NAME}" --public --source=. --remote=origin --push
fi

# ---- kick off the first build ---------------------------------------------
say "starting the first image build"
gh workflow run "Build Razef OS image" --repo "${OWNER}/${REPO_NAME}" || true

cat <<EOF

────────────────────────────────────────────────────────────────────
 Done. What happens now
────────────────────────────────────────────────────────────────────
 1. Watch the build (~40-60 min, the first one is slow):
      gh run watch --repo ${OWNER}/${REPO_NAME}

 2. When it finishes, make the package public (one-time, required
    for anonymous 'bootc switch'):
      open https://github.com/users/${OWNER}/packages/container/${REPO_NAME}/settings
      -> Danger Zone -> Change visibility -> Public

 3. Install it on any Fedora Atomic / Bazzite machine:
      sudo bootc switch ghcr.io/${SLUG}/${REPO_NAME}:stable
      sudo systemctl reboot

 4. Want an ISO instead?
      gh workflow run "Build Razef OS installer ISO" \\
        --repo ${OWNER}/${REPO_NAME} -f image=${REPO_NAME} -f tag=stable
────────────────────────────────────────────────────────────────────
EOF
