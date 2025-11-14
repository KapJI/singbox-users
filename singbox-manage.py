#!/usr/bin/env python3
"""singbox-manage.py — TUI (curses) manager for sing-box users.

Manages:
    - clientsTable.json (name + created date)
    - config.json (updates VLESS inbound users array)

UI keys:
    ↑/↓/PgUp/PgDn  move      a add        e rename      d delete
    s save         S save+restart          c check       x restart
    r reload       q quit (asks to save if dirty)      Ctrl-C behaves like q

Notes:
    - The tool finds VLESS inbound by tag (default: vless-in) or first VLESS inbound.
    - Users are written as: { "uuid": clientId, "name": clientName, "flow": FLOW }
"""

import argparse
from collections.abc import Sequence
import curses
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import TypedDict, cast
import uuid

DEFAULT_CONFIG = "/opt/singbox/config.json"
DEFAULT_TABLE = "/opt/singbox/clientsTable.json"
DEFAULT_TAG = "vless-in"
DEFAULT_FLOW = "xtls-rprx-vision"
DEFAULT_CONTAINER = "singbox"
KEY_BYTE_MAX = 256  # upper bound for single-byte key values


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


def default_config() -> SingBoxConfig:
    """Return an empty sing-box configuration structure."""

    return {}


def default_clients() -> list[ClientEntry]:
    """Return an empty clients table list."""

    return []


def now_ctime() -> str:
    """Return the current time as a formatted string.

    Returns:
        str: Current time in standard ctime format.
    """
    return time.ctime()


def read_json[T_JSON](path: Path, default: T_JSON) -> T_JSON:
    """Read and parse a JSON file with error handling.

    Args:
        path (Path): Path to the JSON file.
        default (T_JSON): Default value to return if file is not found.

    Returns:
        T_JSON: Parsed JSON data or default value if file doesn't exist.

    Raises:
        SystemExit: If JSON cannot be parsed.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            return cast("T_JSON", json.load(f))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERROR: cannot parse JSON at {path}: {e}") from e


def atomic_write_json(path: Path, data: JSONData) -> None:
    """Write JSON data to file atomically to prevent corruption.

    Creates a temporary file first, then replaces the target file.
    Creates parent directories if they don't exist.

    Args:
        path (Path): Target file path.
        data (JSONData): Data to serialize as JSON.
    """
    tmp_path = Path(f"{path}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp_path.replace(path)


def backup(path: Path) -> Path | None:
    """Create a timestamped backup of a file.

    Args:
        path (Path): Path to the file to backup.

    Returns:
        Path | None: Path to the backup file, or None if original doesn't exist.
    """
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_dir = path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"{path.name}.bak.{ts}"
    shutil.copy2(path, dst)
    return dst


def find_vless_inbound(config: SingBoxConfig, tag: str | None) -> int:
    """Find the index of a VLESS inbound in the config.

    Searches by tag first if provided, otherwise returns first VLESS inbound.

    Args:
        config (SingBoxConfig): Parsed sing-box configuration dictionary.
        tag (str | None): Optional VLESS inbound tag to search for.

    Returns:
        int: Index of the VLESS inbound in the inbounds array.

    Raises:
        SystemExit: If no VLESS inbound is found.
    """
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
    """Convert clients table entries to sing-box VLESS user format.

    Args:
        clients (Sequence[ClientEntry]): Client entries from clientsTable.json.
        flow (str): Flow control setting (e.g., 'xtls-rprx-vision').

    Returns:
        list[ConfigUser]: Users that can be written into sing-box config.
    """
    out: list[ConfigUser] = []
    for c in clients:
        uid = c.get("clientId")
        user_data = c.get("userData") or {}
        name = user_data.get("clientName", "client")
        if not uid:
            continue
        out.append({"uuid": uid, "name": name, "flow": flow})
    return out


def clients_from_config_users(
    users: Sequence[ConfigUser],
) -> list[ClientEntry]:
    """Convert sing-box VLESS users to clients table format.

    Args:
        users (Sequence[ConfigUser]): Users from the sing-box config inbound.

    Returns:
        list[ClientEntry]: Client entries suitable for clientsTable.json.
    """
    out: list[ClientEntry] = []
    for u in users:
        uid = u.get("uuid")
        if not uid:
            continue
        name = u.get("name") or "imported"
        out.append(
            {
                "clientId": uid,
                "userData": {"clientName": name, "creationDate": now_ctime()},
            }
        )
    return out


def docker_check_config(config_path: Path) -> tuple[bool, str]:
    """Validate sing-box config using Docker container.

    Args:
        config_path (Path): Path to the config.json file to validate.

    Returns:
        tuple[bool, str]: (success status, output message).
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{config_path}:/etc/sing-box/config.json:ro",
        "ghcr.io/sagernet/sing-box:latest",
        "check",
        "-c",
        "/etc/sing-box/config.json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=25,
            check=False,
        )
        ok = proc.returncode == 0
        return ok, (proc.stdout or "").strip()
    except FileNotFoundError:
        return True, "docker not found; skipped check"
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        return False, f"check error: {e}"


def docker_restart(container: str) -> tuple[bool, str]:
    """Restart a Docker container.

    Args:
        container (str): Name of the Docker container to restart.

    Returns:
        tuple[bool, str]: (success status, output message).
    """
    try:
        proc = subprocess.run(
            ["docker", "restart", container],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=25,
            check=False,
        )
        return proc.returncode == 0, (proc.stdout or "").strip()
    except FileNotFoundError:
        return False, "docker not found"
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        return False, f"restart error: {e}"


class App:
    """Main TUI application for managing sing-box users.

    Attributes:
        stdscr (curses.window): Curses standard screen object.
        config_path (Path): Path to sing-box config.json.
        table_path (Path): Path to clientsTable.json.
        vless_tag (str): VLESS inbound tag to manage.
        flow (str): Flow control setting for VLESS.
        container (str): Docker container name.
        config (SingBoxConfig): Loaded sing-box configuration.
        clients (list[ClientEntry]): List of client entries.
        cursor (int): Current cursor position in the UI.
        message (str): Status message to display.
        dirty (bool): Whether there are unsaved changes.
    """

    def __init__(
        self,
        stdscr: curses.window,
        config_path: Path,
        table_path: Path,
        vless_tag: str,
        container: str,
    ) -> None:
        """Initialize the application with configuration paths and load data.

        Args:
            stdscr: Curses standard screen object.
            config_path (Path): Path to sing-box config.json.
            table_path (Path): Path to clientsTable.json.
            vless_tag (str): VLESS inbound tag to manage.
            container (str): Docker container name for restart operations.
        """
        self.stdscr = stdscr
        self.config_path = config_path
        self.table_path = table_path
        self.config_file = Path(config_path)
        self.table_file = Path(table_path)
        self.vless_tag = vless_tag
        self.flow = DEFAULT_FLOW
        self.container = container

        self.config: SingBoxConfig = read_json(self.config_file, default_config())
        self.clients: list[ClientEntry] = read_json(self.table_file, default_clients())
        if not self.clients:
            try:
                idx = find_vless_inbound(self.config, self.vless_tag)
                inbounds = self.config.get("inbounds", [])
                users = inbounds[idx].get("users", []) if inbounds else []
                if users:
                    self.clients = clients_from_config_users(users)
            except SystemExit:
                pass

        self.cursor = 0
        self.message = "Loaded."
        self.dirty = False

    # ---------- UI helpers ----------
    def draw(self) -> None:
        """Draw the complete TUI interface including title, help text, client list, and status."""
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()

        dirty_mark = " *" if self.dirty else ""
        title = f"Manage Sing-Box  — {self.table_path} | {self.config_path}{dirty_mark}"
        self.stdscr.addnstr(0, 0, title, w - 1, curses.A_BOLD)

        help1 = "↑↓/PgUp/PgDn move  a add  e rename  d delete  s save  S save+restart  c check  x restart  r reload  q quit"
        self.stdscr.addnstr(1, 0, help1, w - 1, curses.A_DIM)

        self.stdscr.addnstr(
            3, 0, f"{'#':>3}  {'UUID':36}  NAME", w - 1, curses.A_UNDERLINE
        )

        start = max(0, min(self.cursor - (h - 8), max(0, len(self.clients) - (h - 8))))
        rows = self.clients[start : start + (h - 8)]
        for idx, c in enumerate(rows):
            i = start + idx
            uid = (c.get("clientId") or "")[:36]
            name = (c.get("userData") or {}).get("clientName", "")
            line = f"{i:>3}  {uid:36}  {name}"
            attr = curses.A_REVERSE if i == self.cursor else 0
            self.stdscr.addnstr(4 + idx, 0, line, w - 1, attr)

        # footer
        self.stdscr.addnstr(
            h - 2, 0, (self.message or "")[: w - 1], w - 1, curses.A_DIM
        )
        self.stdscr.refresh()

    def prompt_line(self, prompt_text: str) -> str | None:
        """Prompt user for text input on the bottom line.

        Args:
            prompt_text (str): Text to display as the prompt.

        Returns:
            Optional[str]: User input string, or None if cancelled.
        """
        curses.echo()
        self.stdscr.nodelay(False)
        h, w = self.stdscr.getmaxyx()
        self.stdscr.move(h - 1, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addnstr(h - 1, 0, f"{prompt_text}: ", w - 1)
        self.stdscr.refresh()
        try:
            s: bytes = self.stdscr.getstr(h - 1, len(prompt_text) + 2, 512)
        except curses.error:
            curses.noecho()
            return None
        curses.noecho()
        if not s:
            return None
        return s.decode("utf-8")

    def prompt_choice(self, prompt_text: str, choices: str) -> str | None:
        """Prompt user to select one character from available choices.

        Args:
            prompt_text (str): Text to display as the prompt.
            choices (str): String of valid choice characters.

        Returns:
            Optional[str]: Selected character, or None if cancelled (Ctrl-C/Esc).
        """
        self.stdscr.nodelay(False)
        h, w = self.stdscr.getmaxyx()
        self.stdscr.move(h - 1, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addnstr(h - 1, 0, f"{prompt_text} [{'/'.join(choices)}]: ", w - 1)
        self.stdscr.refresh()
        while True:
            ch: int = self.stdscr.getch()
            if ch == -1:
                continue
            if ch in (3, 27):  # Ctrl-C / Esc => cancel
                return None
            if 0 <= ch < KEY_BYTE_MAX:
                c = chr(ch).lower()
                if c in choices:
                    return c

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
        name = self.prompt_line(f"Rename [{old}]")
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
        uid = cur.get("clientId", "")
        name = (cur.get("userData") or {}).get("clientName", "")
        ans = self.prompt_choice(f"Delete {name} ({uid})?", "ync")
        if ans == "y":
            self.clients.pop(self.cursor)
            self.cursor = max(0, self.cursor - 1)
            self.dirty = True
            self.message = f"Deleted {name}."
        elif ans == "n":
            self.message = "Not deleted."
        else:
            self.message = "Cancelled."

    def reload_all(self) -> None:
        """Reload configuration and clients from disk, discarding unsaved changes."""
        self.config = read_json(self.config_file, default_config())
        self.clients = read_json(self.table_file, default_clients())
        self.cursor = 0
        self.dirty = False
        self.message = "Reloaded."

    def apply_and_save(self) -> None:
        """Save clients table and update config.json with current users.

        Creates backups of both files before saving.
        """
        try:
            idx = find_vless_inbound(self.config, self.vless_tag)
        except SystemExit as e:
            self.message = str(e)
            return
        users = users_from_clients_table(self.clients, self.flow)

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
        ok, out = docker_check_config(self.config_path)
        tail = (out or "").splitlines()[-1] if out else ""
        self.message = f"check={'OK' if ok else 'FAIL'} {tail}"

    def do_restart(self) -> None:
        """Restart the sing-box Docker container."""
        ok, out = docker_restart(self.container)
        tail = (out or "").strip()
        self.message = f"restart={'OK' if ok else 'FAIL'} {tail}"

    def confirm_quit(self) -> bool:
        """Prompt to save changes before quitting if there are unsaved changes.

        Returns:
            bool: True if it's okay to quit, False to cancel quit.
        """
        if not self.dirty:
            return True
        ans = self.prompt_choice("Unsaved changes. Save before quit?", "ync")
        if ans == "y":
            self.apply_and_save()
            return True
        return ans == "n"

    # ---------- main loop ----------
    def run(self) -> None:
        """Run main event loop handling keyboard input and updating the UI."""
        curses.curs_set(0)
        self.stdscr.nodelay(False)  # blocking getch to avoid flicker
        while True:
            self.draw()
            ch = self.stdscr.getch()
            if ch in (ord("q"), ord("Q"), 3):  # q or Ctrl-C
                if self.confirm_quit():
                    break
                continue
            if ch in (curses.KEY_UP, ord("k")):
                self.cursor = max(0, self.cursor - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.cursor = min(max(0, len(self.clients) - 1), self.cursor + 1)
            elif ch == curses.KEY_PPAGE:
                self.cursor = max(0, self.cursor - 10)
            elif ch == curses.KEY_NPAGE:
                self.cursor = min(max(0, len(self.clients) - 1), self.cursor + 10)
            elif ch in (ord("a"), ord("A")):
                self.add_client()
            elif ch in (ord("e"), ord("E")):
                self.rename_client()
            elif ch in (ord("d"), ord("D")):
                self.delete_client()
            elif ch in (ord("r"), ord("R")):
                self.reload_all()
            elif ch == ord("s"):
                self.apply_and_save()
            elif ch == ord("S"):
                self.apply_and_save()
                self.do_restart()
            elif ch in (ord("c"), ord("C")):
                self.do_check()
            elif ch in (ord("x"), ord("X")):
                self.do_restart()


def main() -> None:
    """Parse command-line arguments and launch the TUI application."""
    ap = argparse.ArgumentParser(
        description="TUI to manage sing-box users and clientsTable.json"
    )
    ap.add_argument(
        "--config",
        type=Path,
        default=Path(DEFAULT_CONFIG),
        help="Path to sing-box config.json",
    )
    ap.add_argument(
        "--table",
        type=Path,
        default=Path(DEFAULT_TABLE),
        help="Path to clientsTable.json",
    )
    ap.add_argument(
        "--vless-tag",
        default=DEFAULT_TAG,
        help="VLESS inbound tag to update (fallback: first VLESS)",
    )
    ap.add_argument(
        "--container",
        default=DEFAULT_CONTAINER,
        help="Docker container name to restart (for S/x)",
    )
    args = ap.parse_args()

    curses.wrapper(
        lambda stdscr: App(
            stdscr, args.config, args.table, args.vless_tag, args.container
        ).run()
    )


if __name__ == "__main__":
    main()
