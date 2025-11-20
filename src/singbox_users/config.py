"""Configuration and data helpers for singbox-users."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime
import json
from pathlib import Path
import shutil
import time
import tomllib
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_CONFIG = Path("/opt/singbox/config.json")
DEFAULT_TABLE = Path("/opt/singbox/clientsTable.json")
DEFAULT_TAG = "vless-in"
DEFAULT_FLOW = "xtls-rprx-vision"
DEFAULT_CONTAINER = "singbox"
DEFAULT_SETTINGS = "settings.toml"
DEFAULT_DOCKER_IMAGE = "ghcr.io/sagernet/sing-box:latest"
DEFAULT_SHARE_DESCRIPTION = "Proxy Server"
DEFAULT_SHARE_DNS1 = "1.1.1.1"
DEFAULT_SHARE_DNS2 = "1.0.0.1"
DEFAULT_SERVER_PORT = 443
DEFAULT_SERVER_SNI = "www.googletagmanager.com"
DEFAULT_QR_CHUNK_SIZE = 850
MAX_QR_CHUNKS = 255
MIN_SERVER_PORT = 1
MAX_SERVER_PORT = 65535

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / DEFAULT_SETTINGS


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


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from settings.toml."""

    vless_tag: str = DEFAULT_TAG
    container: str = DEFAULT_CONTAINER
    docker_image: str = DEFAULT_DOCKER_IMAGE
    config_path: Path = DEFAULT_CONFIG
    clients_table: Path = DEFAULT_TABLE
    server_ip: str = ""
    server_pubkey: str = ""
    server_short_id: str = ""
    share_description: str = DEFAULT_SHARE_DESCRIPTION
    share_dns1: str = DEFAULT_SHARE_DNS1
    share_dns2: str = DEFAULT_SHARE_DNS2
    server_port: int = DEFAULT_SERVER_PORT
    server_sni: str = DEFAULT_SERVER_SNI


def default_config() -> SingBoxConfig:
    """Return an empty sing-box configuration structure."""

    return {}


def default_clients() -> list[ClientEntry]:
    """Return an empty clients table list."""

    return []


def load_settings(path: Path) -> Settings:
    """Load runtime settings from TOML, falling back to defaults when missing."""

    base = Settings()
    if not path.exists():
        return base
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SystemExit(f"ERROR: cannot parse settings at {path}: {e}") from e
    if not isinstance(data, dict):
        raise SystemExit(f"ERROR: settings file {path} must contain a TOML table.")
    overrides: dict[str, object] = {}
    for field in fields(Settings):
        key = field.name
        raw = data.get(key)
        if raw is None:
            continue
        if key == "server_port":
            if not isinstance(raw, int):
                raise SystemExit(
                    f"ERROR: value for '{key}' in {path} must be an integer, got {type(raw)!r}."
                )
            overrides[key] = raw
            continue
        if not isinstance(raw, str):
            raise SystemExit(
                f"ERROR: value for '{key}' in {path} must be a string, got {type(raw)!r}."
            )
        stripped = raw.strip()
        if stripped:
            overrides[key] = Path(stripped) if field.type is Path else stripped
    return replace(base, **overrides)  # type: ignore[arg-type]


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


def users_from_clients_table(
    clients: Sequence[ClientEntry], flow: str
) -> list[ConfigUser]:
    """Convert clients table entries to sing-box VLESS user format."""

    out: list[ConfigUser] = []
    for client in clients:
        uid = client.get("clientId")
        user_data = client.get("userData") or {}
        name = user_data.get("clientName", "client")
        if not uid:
            continue
        out.append({"uuid": uid, "name": name, "flow": flow})
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
