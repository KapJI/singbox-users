"""Runtime settings management for singbox-users."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from pathlib import Path
import tomllib

from singbox_users.singbox_config import (
    DEFAULT_CLIENTS_TABLE,
    DEFAULT_SINGBOX_CONFIG,
    DEFAULT_VLESS_TAG,
)

DEFAULT_SETTINGS_FILENAME = "settings.toml"
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / DEFAULT_SETTINGS_FILENAME

DEFAULT_CONTAINER = "singbox"
DEFAULT_DOCKER_IMAGE = "ghcr.io/sagernet/sing-box:latest"
DEFAULT_SHARE_DESCRIPTION = "Proxy Server"
DEFAULT_SHARE_DNS1 = "1.1.1.1"
DEFAULT_SHARE_DNS2 = "1.0.0.1"
DEFAULT_SERVER_PORT = 443
DEFAULT_SERVER_SNI = "www.googletagmanager.com"
MIN_SERVER_PORT = 1
MAX_SERVER_PORT = 65535


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from settings.toml."""

    vless_tag: str = DEFAULT_VLESS_TAG
    container: str = DEFAULT_CONTAINER
    docker_image: str = DEFAULT_DOCKER_IMAGE
    singbox_config: Path = DEFAULT_SINGBOX_CONFIG
    clients_table: Path = DEFAULT_CLIENTS_TABLE
    server_ip: str = ""
    server_pubkey: str = ""
    server_short_id: str = ""
    share_description: str = DEFAULT_SHARE_DESCRIPTION
    share_dns1: str = DEFAULT_SHARE_DNS1
    share_dns2: str = DEFAULT_SHARE_DNS2
    server_port: int = DEFAULT_SERVER_PORT
    server_sni: str = DEFAULT_SERVER_SNI


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
