#!/bin/bash
# Check out lasgroup/SDPO at the pinned commit and apply patches/sdpo/*.patch
# (see patches/sdpo/README.md). Idempotent: an existing checkout is left
# alone unless it is at the wrong commit, in which case this script stops
# and says so rather than touching it.
#   bash scripts/setup/checkout_sdpo.sh [sdpo_dir]     # default $HOME/SDPO
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SDPO_DIR="${1:-${SDPO_DIR:-${HOME}/SDPO}}"
SDPO_COMMIT="7c457fc"
NPATCH=$(ls "${REPO_ROOT}"/patches/sdpo/*.patch | wc -l)

if [ -d "${SDPO_DIR}/.git" ]; then
    base=$(git -C "${SDPO_DIR}" rev-parse --short "HEAD~${NPATCH}" 2>/dev/null || true)
    if [ "${base}" = "${SDPO_COMMIT}" ]; then
        echo "[skip] ${SDPO_DIR} is at ${SDPO_COMMIT} + ${NPATCH} patches"
        exit 0
    fi
    echo "ERROR: ${SDPO_DIR} exists but HEAD~${NPATCH} is '${base}', not ${SDPO_COMMIT}." >&2
    echo "       Move it aside or fix it by hand (patches/sdpo/README.md)." >&2
    exit 1
fi

echo "Cloning SDPO into ${SDPO_DIR} at ${SDPO_COMMIT}"
git clone --quiet https://github.com/lasgroup/SDPO.git "${SDPO_DIR}"
git -C "${SDPO_DIR}" checkout --quiet "${SDPO_COMMIT}"
# git am needs a committer identity; a throwaway one keeps this hands-off.
git -C "${SDPO_DIR}" -c user.name="moralgym-setup" -c user.email="moralgym-setup@localhost" \
    am "${REPO_ROOT}"/patches/sdpo/*.patch
echo "SDPO ready: $(git -C "${SDPO_DIR}" log --oneline -1)"
