"""Helpers for building shareable vpn:// payloads."""

from __future__ import annotations

import base64
import json
import math
import struct
from typing import Final
import zlib

from .config import (
    DEFAULT_QR_CHUNK_SIZE,
    DEFAULT_SERVER_PORT,
    DEFAULT_SHARE_DESCRIPTION,
    DEFAULT_SHARE_DNS1,
    DEFAULT_SHARE_DNS2,
    MAX_QR_CHUNKS,
    JSONObject,
)

QR_MAGIC_QINT16: Final = 1984


def qcompress(payload: bytes, level: int = 8) -> bytes:
    """Qt-compatible qCompress wrapper (length prefix + zlib)."""

    return struct.pack(">I", len(payload)) + zlib.compress(payload, level)


def vpn_url_from_qcompressed(qc: bytes) -> str:
    """Return vpn:// URL representation derived from qCompressed bytes."""

    token = base64.urlsafe_b64encode(qc).decode().rstrip("=")
    return f"vpn://{token}"


def make_qr_chunks(qc: bytes, chunk_size: int = DEFAULT_QR_CHUNK_SIZE) -> list[str]:
    """Split qCompressed bytes into Base64 payloads matching Amnezia QR format."""

    total = len(qc)
    chunks = max(1, math.ceil(total / chunk_size))
    if chunks > MAX_QR_CHUNKS:
        raise ValueError(
            f"Too many chunks ({chunks}); increase chunk size from {chunk_size}"
        )
    payloads: list[str] = []
    for idx, start in enumerate(range(0, total, chunk_size)):
        part = qc[start : start + chunk_size]
        buf = bytearray()
        buf += struct.pack(">h", QR_MAGIC_QINT16)
        buf += struct.pack(">B", chunks)
        buf += struct.pack(">B", idx)
        buf += struct.pack(">I", len(part))
        buf += part
        payloads.append(base64.urlsafe_b64encode(bytes(buf)).decode().rstrip("="))
    return payloads


def build_inner_share_config(
    server_ip: str,
    client_id: str,
    server_pubkey: str,
    server_short_id: str,
    *,
    port: int = DEFAULT_SERVER_PORT,
    server_name: str,
) -> JSONObject:
    """Return the Amnezia inner xray config for a single client."""

    return {
        "log": {"loglevel": "error"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True},
            }
        ],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": server_ip,
                            "port": port,
                            "users": [
                                {
                                    "id": client_id,
                                    "flow": "xtls-rprx-vision",
                                    "encryption": "none",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "fingerprint": "chrome",
                        "serverName": server_name,
                        "publicKey": server_pubkey,
                        "shortId": server_short_id,
                        "spiderX": "",
                    },
                },
            }
        ],
    }


def build_outer_share_config(
    server_ip: str,
    client_id: str,
    server_pubkey: str,
    server_short_id: str,
    *,
    description: str = DEFAULT_SHARE_DESCRIPTION,
    dns1: str = DEFAULT_SHARE_DNS1,
    dns2: str = DEFAULT_SHARE_DNS2,
    container: str,
    port: int = DEFAULT_SERVER_PORT,
    server_name: str,
) -> JSONObject:
    """Wrap the inner xray config into Amnezia's outer JSON structure."""

    inner = build_inner_share_config(
        server_ip,
        client_id,
        server_pubkey,
        server_short_id,
        port=port,
        server_name=server_name,
    )
    last_config_str = json.dumps(inner, indent=4, ensure_ascii=False) + "\n"
    return {
        "containers": [
            {
                "container": container,
                "xray": {
                    "last_config": last_config_str,
                    "port": str(port),
                    "transport_proto": "tcp",
                },
            }
        ],
        "defaultContainer": container,
        "description": description,
        "dns1": dns1,
        "dns2": dns2,
        "hostName": server_ip,
    }
