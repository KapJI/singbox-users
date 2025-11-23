"""Share-link workflow that integrates qr generation, clipboard, and modals."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

from imgcat import imgcat  # type: ignore[import-untyped]
from qrcode import QRCode
from qrcode.constants import ERROR_CORRECT_L
from qrcode.image.pil import PilImage

from singbox_users.settings import DEFAULT_SETTINGS_PATH, Settings
from singbox_users.share_payload import (
    build_outer_share_config,
    make_qr_chunks,
    qcompress,
    vpn_url_from_qcompressed,
)
from singbox_users.singbox_config import (
    ServerSettings,
    SingBoxConfig,
    extract_server_settings,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from singbox_users.ui.dialogs import ModalManager

OSC52_MAX_PAYLOAD = 120000


class ShareFlow:
    """Encapsulate vpn:// generation + interactive prompts."""

    def __init__(
        self,
        settings: Settings,
        modal: ModalManager,
        suspend_curses: Callable[[], AbstractContextManager[None]],
        singbox_config: SingBoxConfig,
    ) -> None:
        """Store collaborators needed to drive the share workflow."""

        self.settings = settings
        self.modal = modal
        self.suspend_curses = suspend_curses
        self.singbox_config = singbox_config

    def share_client(self, client_id: str) -> str:
        """Run the share modal loop and return the resulting status message."""

        vpn_url, outer_json, qr_payloads = self._build_share_payload(client_id)
        show_qr, status = self._show_share_modal(vpn_url)
        if show_qr:
            status = self._display_qr_series(vpn_url, outer_json, qr_payloads)
        return status or "Cancelled."

    def _build_share_payload(self, client_id: str) -> tuple[str, str, list[str]]:
        server_ip = self._require_server_ip()
        server_settings = self._load_server_settings()
        outer = build_outer_share_config(
            server_ip,
            client_id,
            server_settings.public_key,
            server_settings.short_id,
            description=self.settings.share_description,
            dns1=self.settings.share_dns1,
            dns2=self.settings.share_dns2,
            port=server_settings.port,
            server_name=server_settings.server_name,
        )
        outer_json = json.dumps(outer, indent=4, ensure_ascii=False) + "\n"
        qc = qcompress(outer_json.encode("utf-8"), level=8)
        url = vpn_url_from_qcompressed(qc)
        try:
            qr_payloads = make_qr_chunks(qc)
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise ValueError(f"Failed to split QR chunks: {exc}") from exc
        return url, outer_json, qr_payloads

    def _copy_to_clipboard(self, text: str) -> None:
        data = text.encode("utf-8")
        if self._copy_via_tmux(data):
            return
        b64 = base64.b64encode(data).decode("ascii")
        if len(b64) > OSC52_MAX_PAYLOAD:
            raise RuntimeError("clipboard payload exceeds OSC-52 limits")
        osc_sequence = f"\033]52;c;{b64}\a"
        try:
            with Path("/dev/tty").open("w", encoding="utf-8") as tty:
                tty.write(osc_sequence)
                tty.flush()
        except OSError as exc:  # pragma: no cover - depends on tty availability
            raise RuntimeError(f"clipboard unavailable ({exc})") from exc

    def _show_share_modal(self, vpn_url: str) -> tuple[bool, str]:
        header = "Client config ready."
        status_line = "Cancelled."

        while True:
            result = self.modal.prompt_buttons(
                f"{header}",
                [("Copy link", "copy"), ("Show QR", "show"), ("Close", "close")],
            )
            if result == "copy":
                try:
                    self._copy_to_clipboard(vpn_url)
                    header = "Share link copied to clipboard."
                    status_line = header
                except RuntimeError as exc:
                    header = str(exc)
                    status_line = header
                continue
            if result == "show":
                return True, ""
            return False, status_line

    def _display_qr_series(
        self,
        vpn_url: str,
        outer_json: str,
        payloads: list[str],
    ) -> str:
        if not payloads:
            return "No QR payloads to display."

        def build_image(payload: str) -> PilImage:
            qr = QRCode(
                error_correction=ERROR_CORRECT_L,
                box_size=6,
                border=2,
            )
            qr.add_data(payload)
            qr.make(fit=True)
            return qr.make_image(
                image_factory=PilImage,
                fill_color="black",
                back_color="white",
            )

        with self.suspend_curses():
            print("Exported JSON:\n", outer_json, sep="", flush=True)
            print("Share link:", vpn_url, "\n", flush=True)
            total = len(payloads)
            for idx, payload in enumerate(payloads, 1):
                img = build_image(payload)
                try:
                    imgcat(img.get_image(), height=20)
                except (OSError, RuntimeError) as exc:
                    return f"imgcat rendering failed: {exc}"
                print(f"[QR {idx}/{total}] {len(payload)} chars", flush=True)
                if idx < total:
                    resp = input("Press Enter for next QR (q to stop): ")
                    if resp.strip().lower().startswith("q"):
                        break
            input("Press Enter to return to singbox-users...")
        return "QR display finished."

    def _copy_via_tmux(self, data: bytes) -> bool:
        if not os.environ.get("TMUX"):
            return False
        cmd = [
            "tmux",
            "load-buffer",
            "-w",
            "-b",
            "singbox-users",
            "-",
        ]
        try:
            result = subprocess.run(cmd, input=data, check=False)
        except OSError:
            return False
        return result.returncode == 0

    def _load_server_settings(self) -> ServerSettings:
        try:
            return extract_server_settings(self.singbox_config, self.settings.vless_tag)
        except (SystemExit, ValueError) as exc:
            raise ValueError(str(exc)) from exc

    def _require_server_ip(self) -> str:
        server_ip = self.settings.server_ip.strip()
        if server_ip:
            return server_ip
        raise ValueError(f"Missing server_ip in {DEFAULT_SETTINGS_PATH}.")
