#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TARGET="${1:-/usr/local/bin/singbox-manage}"
TARGET_DIR=$(dirname -- "${TARGET}")
SOURCE="${SCRIPT_DIR}/singbox-manage.py"

if [[ ! -f "${SOURCE}" ]]; then
  echo "ERROR: singbox-manage.py not found next to install.sh" >&2
  exit 1
fi

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
exec "\${UV_BIN}" run --project "\${REPO_DIR}" "\${REPO_DIR}/singbox-manage.py" "\$@"
EOF

chmod +x -- "${TARGET}"

echo "Installed wrapper: ${TARGET} (uses uv run in ${SCRIPT_DIR})"
