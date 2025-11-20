"""Configuration and data helpers for singbox-users."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import time
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_SINGBOX_CONFIG = Path("/opt/singbox/config.json")
DEFAULT_CLIENTS_TABLE = Path("/opt/singbox/clientsTable.json")
DEFAULT_VLESS_TAG = "vless-in"
DEFAULT_FLOW = "xtls-rprx-vision"


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
