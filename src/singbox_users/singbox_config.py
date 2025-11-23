"""Configuration and data helpers for singbox-users."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
import time
from typing import TYPE_CHECKING, TypedDict, cast

from nacl.bindings import crypto_scalarmult_base

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_SINGBOX_CONFIG = Path("/opt/singbox/config.json")
DEFAULT_CLIENTS_TABLE = Path("/opt/singbox/clientsTable.json")
DEFAULT_VLESS_TAG = "vless-in"
DEFAULT_FLOW = "xtls-rprx-vision"
REALITY_KEY_BYTES = 32


class ClientUserData(TypedDict, total=False):
    """User metadata stored alongside each client entry."""

    clientName: str
    creationDate: str


class ClientEntry(TypedDict, total=False):
    """Representation of a row in clientsTable.json."""

    clientId: str
    userData: ClientUserData


class ConfigUser(TypedDict, total=False):
    """Sing-box VLESS user entry."""

    uuid: str
    name: str
    flow: str


class Inbound(TypedDict, total=False):
    """Sing-box inbound definition."""

    type: str
    tag: str
    users: list[ConfigUser]


class SingBoxConfig(TypedDict, total=False):
    """Sing-box configuration root structure."""

    inbounds: list[Inbound]


@dataclass(frozen=True)
class ServerSettings:
    """Subset of Reality/VLESS fields required for share links."""

    port: int
    server_name: str
    short_id: str
    public_key: str


JSONData = SingBoxConfig | list[ClientEntry]
type JSONValue = (
    str | int | float | bool | None | dict[str, "JSONValue"] | list["JSONValue"]
)
type JSONObject = dict[str, JSONValue]


def default_config() -> SingBoxConfig:
    """Return an empty sing-box configuration structure."""

    return {}


def default_clients() -> list[ClientEntry]:
    """Return an empty clients table list."""

    return []


def now_ctime() -> str:
    """Return the current time as a formatted string."""

    return time.ctime()


def read_json[T_JSON](path: Path, default: T_JSON) -> T_JSON:
    """Read and parse a JSON file with error handling."""

    try:
        with path.open("r", encoding="utf-8") as f:
            return cast("T_JSON", json.load(f))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERROR: cannot parse JSON at {path}: {e}") from e


def atomic_write_json(path: Path, data: JSONData) -> None:
    """Write JSON data to file atomically to prevent corruption."""

    tmp_path = Path(f"{path}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp_path.replace(path)


def backup(path: Path) -> Path | None:
    """Create a timestamped backup of a file."""

    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_dir = path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"{path.name}.bak.{ts}"
    shutil.copy2(path, dst)
    return dst


def find_vless_inbound(config: SingBoxConfig, tag: str | None) -> int:
    """Find the index of a VLESS inbound in the config."""

    inbounds = config.get("inbounds", [])
    if tag:
        for i, ib in enumerate(inbounds):
            if ib.get("type") == "vless" and ib.get("tag") == tag:
                return i
    for i, ib in enumerate(inbounds):
        if ib.get("type") == "vless":
            return i
    raise SystemExit("No VLESS inbound found in config.json")


def users_from_clients_table(clients: Sequence[ClientEntry]) -> list[ConfigUser]:
    """Convert clients table entries to sing-box VLESS user format."""

    out: list[ConfigUser] = []
    for client in clients:
        uid = client.get("clientId")
        user_data = client.get("userData") or {}
        name = user_data.get("clientName", "client")
        if not uid:
            continue
        out.append({"uuid": uid, "name": name, "flow": DEFAULT_FLOW})
    return out


def clients_from_config_users(
    users: Sequence[ConfigUser],
) -> list[ClientEntry]:
    """Convert sing-box VLESS users to clients table format."""

    out: list[ClientEntry] = []
    for user in users:
        uid = user.get("uuid")
        if not uid:
            continue
        name = user.get("name") or "imported"
        out.append(
            {
                "clientId": uid,
                "userData": {"clientName": name, "creationDate": now_ctime()},
            }
        )
    return out


def extract_server_settings(config: SingBoxConfig, tag: str | None) -> ServerSettings:
    """Return Reality parameters from a sing-box VLESS inbound."""

    idx = find_vless_inbound(config, tag)
    inbounds = config.get("inbounds", [])
    if idx >= len(inbounds):
        raise ValueError("VLESS inbound missing from configuration.")
    inbound = inbounds[idx]
    port = _coerce_port(inbound.get("listen_port"))
    if port is None:
        raise ValueError("listen_port is required on the VLESS inbound.")

    tls = cast("dict[str, object]", inbound.get("tls") or {})
    reality = cast("dict[str, object]", tls.get("reality") or {})
    handshake = cast("dict[str, object]", reality.get("handshake") or {})

    server_name = _require_string(
        handshake.get("server"),
        "tls.reality.handshake.server",
    )
    short_id = _extract_short_id(cast("list[str]", reality.get("short_id") or []))
    private_key = _require_string(
        reality.get("private_key"),
        "tls.reality.private_key",
    )
    public_key = _derive_reality_public_key(private_key)
    return ServerSettings(
        port=port,
        server_name=server_name,
        short_id=short_id,
        public_key=public_key,
    )


def _coerce_port(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped, 10)
        except ValueError:
            return None
    return None


def _extract_short_id(ids: list[str]) -> str:
    if not ids:
        raise ValueError("tls.reality.short_id must contain at least one value.")
    return ids[0].strip()


def _derive_reality_public_key(private_key: str) -> str:
    scalar = _decode_base64(private_key)
    if len(scalar) != REALITY_KEY_BYTES:
        raise ValueError("Reality private key must decode to 32 bytes.")
    point = crypto_scalarmult_base(scalar)
    return base64.urlsafe_b64encode(point).decode("ascii").rstrip("=")


def _decode_base64(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except binascii.Error as exc:
        raise ValueError("Reality key must be base64-encoded.") from exc


def _require_string(value: object, label: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"Missing {label} in config.json.")
