"""Share-link workflow that integrates qr generation, clipboard, and modals."""

from __future__ import annotations

import base64
import importlib
import io
import json
from pathlib import Path
import shutil
import subprocess
from typing import TYPE_CHECKING

from singbox_users.settings import (
    DEFAULT_SETTINGS_PATH,
    MAX_SERVER_PORT,
    MIN_SERVER_PORT,
    Settings,
)
from singbox_users.share_payload import (
    build_outer_share_config,
    make_qr_chunks,
    qcompress,
    vpn_url_from_qcompressed,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from types import ModuleType

    from singbox_users.ui.dialogs import ModalManager

OSC52_MAX_PAYLOAD = 120000


class ShareFlow:
    """Encapsulate vpn:// generation + interactive prompts."""

    def __init__(
        self,
        settings: Settings,
        modal: ModalManager,
        suspend_curses: Callable[[], AbstractContextManager[None]],
    ) -> None:
        """Store collaborators needed to drive the share workflow."""

        self.settings = settings
        self.modal = modal
        self.suspend_curses = suspend_curses

    def share_client(self, client_id: str) -> str:
        """Run the share modal loop and return the resulting status message."""

        vpn_url, qr_payloads = self._build_share_payload(client_id)
        show_qr, status = self._show_share_modal(vpn_url)
        if show_qr:
            status = self._display_qr_series(vpn_url, qr_payloads)
        return status or "Cancelled."

    def _build_share_payload(self, client_id: str) -> tuple[str, list[str]]:
        missing: list[str] = []
        server_ip = self.settings.server_ip
        server_pubkey = self.settings.server_pubkey
        server_short_id = self.settings.server_short_id
        if not server_ip:
            missing.append("server_ip")
        if not server_pubkey:
            missing.append("server_pubkey")
        if not server_short_id:
            missing.append("server_short_id")
        if missing:
            joined = ", ".join(missing)
            raise ValueError(
                f"Missing settings for share command: {joined}. Update {DEFAULT_SETTINGS_PATH}"
            )
        server_port = self.settings.server_port
        if not MIN_SERVER_PORT <= server_port <= MAX_SERVER_PORT:
            raise ValueError(
                f"Server_port must be between {MIN_SERVER_PORT} and {MAX_SERVER_PORT}."
            )
        outer = build_outer_share_config(
            server_ip,
            client_id,
            server_pubkey,
            server_short_id,
            description=self.settings.share_description,
            dns1=self.settings.share_dns1,
            dns2=self.settings.share_dns2,
            container=self.settings.container,
            port=self.settings.server_port,
            server_name=self.settings.server_sni,
        )
        outer_json = json.dumps(outer, indent=4, ensure_ascii=False) + "\n"
        qc = qcompress(outer_json.encode("utf-8"), level=8)
        url = vpn_url_from_qcompressed(qc)
        try:
            qr_payloads = make_qr_chunks(qc)
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise ValueError(f"Failed to split QR chunks: {exc}") from exc
        return url, qr_payloads

    def _copy_to_clipboard(self, text: str) -> None:
        data = text.encode("utf-8")
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

    def _display_qr_series(self, vpn_url: str, payloads: list[str]) -> str:
        if not payloads:
            return "No QR payloads to display."
        backend = self._load_qrcode_backend()
        if backend is None:
            return "qrcode dependency missing. Install 'qrcode' extras."
        qrcode_module, error_correct_l, pil_image = backend
        imgcat = shutil.which("imgcat")
        if not imgcat:
            return "imgcat command not found in PATH."

        def build_png(payload: str) -> bytes:
            qr = qrcode_module.QRCode(
                error_correction=error_correct_l,
                box_size=6,
                border=2,
            )
            qr.add_data(payload)
            qr.make(fit=True)
            img = qr.make_image(
                image_factory=pil_image,
                fill_color="black",
                back_color="white",
            )
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        with self.suspend_curses():
            print("Share link:", vpn_url, "\n", flush=True)
            total = len(payloads)
            for idx, payload in enumerate(payloads, 1):
                png = build_png(payload)
                subprocess.run([imgcat], input=png, check=False)
                print(f"[QR {idx}/{total}] {len(payload)} chars", flush=True)
                if idx < total:
                    resp = input("Press Enter for next QR (q to stop): ")
                    if resp.strip().lower().startswith("q"):
                        break
            input("Press Enter to return to singbox-users...")
        return "QR display finished."

    def _load_qrcode_backend(self) -> tuple[ModuleType, int, type[object]] | None:
        try:
            qrcode_module = importlib.import_module("qrcode")
            constants = importlib.import_module("qrcode.constants")
            pil = importlib.import_module("qrcode.image.pil")
        except ImportError:
            return None
        return qrcode_module, constants.ERROR_CORRECT_L, pil.PilImage
