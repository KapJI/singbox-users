#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TARGET="${1:-/usr/local/bin/singbox-users}"
TARGET_DIR=$(dirname -- "${TARGET}")

mkdir -p -- "${TARGET_DIR}"
cat >"${TARGET}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="${SCRIPT_DIR}"
UV_BIN="\${UV_BIN:-uv}"
if ! command -v "\${UV_BIN}" >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install uv or set UV_BIN." >&2
  exit 1
fi
exec "\${UV_BIN}" run --project "\${REPO_DIR}" singbox-users "\$@"
EOF

chmod +x -- "${TARGET}"

echo "Installed wrapper: ${TARGET} (uses uv run in ${SCRIPT_DIR})"
