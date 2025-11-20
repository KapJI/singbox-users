"""Curses TUI application for singbox-users."""

from __future__ import annotations

import argparse
import contextlib
from contextlib import AbstractContextManager
import curses
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from .docker_utils import check_config, restart_container
from .settings import DEFAULT_SETTINGS_PATH, Settings, load_settings
from .share_flow import ShareFlow
from .singbox_config import (
    DEFAULT_CLIENTS_TABLE,
    DEFAULT_SINGBOX_CONFIG,
    ClientEntry,
    SingBoxConfig,
    atomic_write_json,
    backup,
    clients_from_config_users,
    default_clients,
    default_config,
    find_vless_inbound,
    now_ctime,
    read_json,
    users_from_clients_table,
)
from .terminal import suspend_curses
from .ui.dialogs import MARK_BOLD_OFF, MARK_BOLD_ON, ModalManager
from .ui.layout import MainView
from .ui.theme import init_styles

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

with contextlib.suppress(AttributeError):
    curses.set_escdelay(25)


@dataclass(frozen=True)
class CommandBinding:
    """Registry entry describing a keyboard shortcut and its behavior."""

    display: str
    keys: tuple[int, ...]
    handler: Callable[[], bool]
    show_in_help: bool = True


class App:
    """Main TUI application for managing sing-box users."""

    def __init__(
        self,
        stdscr: curses.window,
        settings: Settings,
    ) -> None:
        """Initialize the application with configuration paths and load data.

        Args:
            stdscr: Curses standard screen object.
            settings (Settings): Runtime configuration loaded from TOML and CLI overrides.
        """
        self.stdscr = stdscr
        self.settings = settings
        self.config_path = Path(self.settings.singbox_config)
        self.table_path = Path(self.settings.clients_table)
        self.config_file = self.config_path
        self.table_file = self.table_path

        self.config: SingBoxConfig = read_json(self.config_file, default_config())
        self.clients: list[ClientEntry] = read_json(self.table_file, default_clients())
        if not self.clients:
            try:
                idx = find_vless_inbound(self.config, self.settings.vless_tag)
                inbounds = self.config.get("inbounds", [])
                users = inbounds[idx].get("users", []) if inbounds else []
                if users:
                    self.clients = clients_from_config_users(users)
            except SystemExit:
                pass

        self.cursor = 0
        self.view_top = 0
        self.message = "Loaded."
        self.dirty = False
        self.command_bindings: tuple[CommandBinding, ...] = (
            self._build_command_bindings()
        )
        self.command_map = self._build_command_map(self.command_bindings)
        self.styles = init_styles()
        self.main_view = MainView(
            self.stdscr,
            self.styles,
            self.table_path,
            self.config_path,
        )
        self.modal = ModalManager(self.stdscr, self.styles)

        def suspend_fn() -> AbstractContextManager[None]:
            return suspend_curses(self.stdscr)

        self.share_flow = ShareFlow(self.settings, self.modal, suspend_fn)
        with contextlib.suppress(curses.error):
            self.stdscr.bkgd(" ", self.styles.get("background", 0))

    # ---------- UI helpers ----------
    def draw(self) -> None:
        """Render the primary screen through the shared MainView renderer."""

        self.view_top = self.main_view.draw(
            self.command_bindings,
            self.clients,
            self.cursor,
            self.view_top,
            self.message,
            self.dirty,
        )

    def _build_command_bindings(self) -> tuple[CommandBinding, ...]:
        """Create the registry describing shortcuts, help text, and handlers."""

        def run(action: Callable[[], None]) -> Callable[[], bool]:
            def runner() -> bool:
                action()
                return False

            return runner

        def move(delta: int, wrap: bool = False) -> Callable[[], bool]:
            def runner() -> bool:
                self.move_cursor(delta, wrap=wrap)
                return False

            return runner

        def save_and_restart() -> bool:
            self.apply_and_save()
            self.do_restart()
            return False

        def request_quit() -> bool:
            return self.confirm_quit()

        return (
            CommandBinding("↑/k move up", (curses.KEY_UP, ord("k")), move(-1, True)),
            CommandBinding("↓/j move down", (curses.KEY_DOWN, ord("j")), move(1, True)),
            CommandBinding("PgUp prev page", (curses.KEY_PPAGE,), move(-10)),
            CommandBinding("PgDn next page", (curses.KEY_NPAGE,), move(10)),
            CommandBinding("a add", (ord("a"), ord("A")), run(self.add_client)),
            CommandBinding("e rename", (ord("e"), ord("E")), run(self.rename_client)),
            CommandBinding(
                "g share link/QR", (ord("g"), ord("G")), run(self.share_current_client)
            ),
            CommandBinding("d delete", (ord("d"), ord("D")), run(self.delete_client)),
            CommandBinding("s save", (ord("s"),), run(self.apply_and_save)),
            CommandBinding("S save and restart", (ord("S"),), save_and_restart),
            CommandBinding("c check", (ord("c"), ord("C")), run(self.do_check)),
            CommandBinding("x restart", (ord("x"), ord("X")), run(self.do_restart)),
            CommandBinding("r reload", (ord("r"), ord("R")), run(self.reload_all)),
            CommandBinding("q quit", (ord("q"), ord("Q"), 3), request_quit),
        )

    def _build_command_map(
        self, bindings: Sequence[CommandBinding]
    ) -> dict[int, Callable[[], bool]]:
        """Create a direct lookup table for key codes to handlers."""

        table: dict[int, Callable[[], bool]] = {}
        for binding in bindings:
            for key in binding.keys:
                if key in table:
                    raise ValueError(
                        f"Duplicate command key detected: {key} for '{binding.display}'"
                    )
                table[key] = binding.handler
        return table

    def dispatch_command(self, key: int) -> bool:
        """Dispatch a keypress via the registry. Returns True if the app should exit."""

        if key == -1:
            return False
        handler = self.command_map.get(key)
        return handler() if handler else False

    def prompt_line(
        self, prompt_text: str, initial_text: str | None = None
    ) -> str | None:
        """Mirror legacy API while delegating to ModalManager."""

        return self.modal.prompt_line(prompt_text, initial_text)

    def prompt_choice(self, prompt_text: str, choices: str) -> str | None:
        """Expose compatibility wrapper around ModalManager.prompt_choice."""

        return self.modal.prompt_choice(prompt_text, choices)

    def prompt_buttons(
        self, prompt_text: str, buttons: Sequence[tuple[str, str]]
    ) -> str | None:
        """Delegate button modals to the shared ModalManager instance."""

        return self.modal.prompt_buttons(prompt_text, buttons)

    # ---------- actions ----------
    def add_client(self) -> None:
        """Add a new client with a generated UUID."""
        name = self.prompt_line("Client name")
        if not name:
            self.message = "Add cancelled."
            return
        uid = str(uuid.uuid4())
        self.clients.append(
            {
                "clientId": uid,
                "userData": {"clientName": name.strip(), "creationDate": now_ctime()},
            }
        )
        self.cursor = len(self.clients) - 1
        self.dirty = True
        self.message = f"Added {name} ({uid})."

    def rename_client(self) -> None:
        """Rename the currently selected client."""
        if not self.clients:
            self.message = "No clients."
            return
        cur = self.clients[self.cursor]
        old = (cur.get("userData") or {}).get("clientName", "")
        name = self.prompt_line("Rename client", old)
        if not name:
            self.message = "Rename cancelled."
            return
        cur["userData"]["clientName"] = name.strip()
        self.dirty = True
        self.message = f"Renamed to {name}."

    def delete_client(self) -> None:
        """Delete the currently selected client after confirmation."""
        if not self.clients:
            self.message = "No clients."
            return
        cur = self.clients[self.cursor]
        name = (cur.get("userData") or {}).get("clientName", "")
        ans = self.prompt_buttons(
            f"Delete {MARK_BOLD_ON}{name}{MARK_BOLD_OFF}?",
            [
                ("Delete", "y"),
                ("Cancel", "c"),
            ],
        )
        if ans == "y":
            self.clients.pop(self.cursor)
            self.cursor = max(0, self.cursor - 1)
            self.dirty = True
            self.message = f"Deleted {name}."
        else:
            self.message = "Cancelled."

    def reload_all(self) -> None:
        """Reload configuration and clients from disk, discarding unsaved changes."""
        if self.dirty and not self._confirm_discard_or_save():
            self.message = "Reload cancelled."
            return
        self.config = read_json(self.config_file, default_config())
        self.clients = read_json(self.table_file, default_clients())
        self.cursor = 0
        self.view_top = 0
        self.dirty = False
        self.message = "Reloaded."

    def apply_and_save(self) -> None:
        """Save clients table and update config.json with current users.

        Creates backups of both files before saving.
        """
        try:
            idx = find_vless_inbound(self.config, self.settings.vless_tag)
        except SystemExit as e:
            self.message = str(e)
            return
        users = users_from_clients_table(self.clients)

        b1 = backup(self.table_file)
        b2 = backup(self.config_file)

        atomic_write_json(self.table_file, self.clients)
        inbounds = self.config.get("inbounds", [])
        inbounds[idx]["users"] = users
        self.config["inbounds"] = inbounds
        atomic_write_json(self.config_file, self.config)

        self.dirty = False
        backup_notes: list[str] = []
        if b1:
            rel = b1.relative_to(self.table_file.parent)
            backup_notes.append(f"table→{rel}")
        if b2:
            rel = b2.relative_to(self.config_file.parent)
            backup_notes.append(f"config→{rel}")

        if backup_notes:
            self.message = "Saved. Backups: " + ", ".join(backup_notes)
        else:
            self.message = "Saved."

    def do_check(self) -> None:
        """Validate the sing-box configuration using Docker."""
        ok, out = check_config(self.config_path, self.settings.docker_image)
        tail = (out or "").splitlines()[-1] if out else ""
        self.message = f"check={'OK' if ok else 'FAIL'} {tail}"

    def do_restart(self) -> None:
        """Restart the sing-box Docker container."""
        ok, out = restart_container(self.settings.container)
        tail = (out or "").strip()
        self.message = f"restart={'OK' if ok else 'FAIL'} {tail}"

    def share_current_client(self) -> None:
        """Generate a vpn:// link and optional QR codes for the selected client."""
        if not self.clients:
            self.message = "No clients."
            return
        client = self.clients[self.cursor]
        client_id = client.get("clientId") or ""
        if not client_id:
            self.message = "Selected client has no UUID."
            return
        try:
            self.message = self.share_flow.share_client(client_id)
        except ValueError as exc:
            self.message = str(exc)

    def move_cursor(self, delta: int, wrap: bool = False) -> None:
        """Move selection cursor by delta, optionally wrapping around list bounds."""
        total = len(self.clients)
        if total == 0:
            self.cursor = 0
            return
        if wrap:
            self.cursor = (self.cursor + delta) % total
        else:
            self.cursor = min(max(0, self.cursor + delta), total - 1)

    def confirm_quit(self) -> bool:
        """Prompt to save changes before quitting if there are unsaved changes.

        Returns:
            bool: True if it's okay to quit, False to cancel quit.
        """
        if not self.dirty:
            return True
        ans = self.prompt_buttons(
            "Unsaved changes detected. How should we proceed?",
            [
                ("Save & Quit", "y"),
                ("Discard", "n"),
                ("Cancel", "c"),
            ],
        )
        if ans == "y":
            self.apply_and_save()
            return True
        return ans == "n"

    def _confirm_discard_or_save(self) -> bool:
        ans = self.prompt_buttons(
            "Unsaved changes detected. Save before proceeding?",
            [
                ("Save", "y"),
                ("Discard", "n"),
                ("Cancel", "c"),
            ],
        )
        if ans == "y":
            self.apply_and_save()
            return True
        return ans == "n"

    # ---------- main loop ----------
    def run(self) -> None:
        """Run main event loop handling keyboard input and updating the UI."""
        curses.curs_set(0)
        self.stdscr.nodelay(False)
        while True:
            self.draw()
            try:
                ch = self.stdscr.getch()
            except KeyboardInterrupt:
                ch = 3  # emulate Ctrl-C keypress
            if self.dispatch_command(ch):
                break


def main() -> None:
    """Parse command-line arguments and launch the TUI application."""
    ap = argparse.ArgumentParser(
        description="TUI to manage sing-box users and clientsTable.json"
    )
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"Path to sing-box config.json (default: {DEFAULT_SINGBOX_CONFIG})",
    )
    ap.add_argument(
        "--clients-table",
        type=Path,
        default=None,
        metavar="PATH",
        dest="clients_table",
        help=f"Path to clientsTable.json (default: {DEFAULT_CLIENTS_TABLE})",
    )
    args = ap.parse_args()

    settings = load_settings(DEFAULT_SETTINGS_PATH)
    if args.config is not None:
        settings = replace(settings, singbox_config=args.config)
    if args.clients_table is not None:
        settings = replace(settings, clients_table=args.clients_table)

    curses.wrapper(lambda stdscr: App(stdscr, settings).run())


if __name__ == "__main__":
    main()
