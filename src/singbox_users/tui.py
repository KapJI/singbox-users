"""Curses TUI application for singbox-users."""

from __future__ import annotations

import argparse
import base64
import contextlib
import curses
from dataclasses import dataclass, replace
import io
import json
from pathlib import Path
import shutil
import subprocess
from typing import TYPE_CHECKING
import uuid

import qrcode
from qrcode.constants import ERROR_CORRECT_L
from qrcode.image.pil import PilImage

from .config import (
    DEFAULT_CONFIG,
    DEFAULT_FLOW,
    DEFAULT_SERVER_SNI,
    DEFAULT_SETTINGS_PATH,
    DEFAULT_TABLE,
    MAX_SERVER_PORT,
    MIN_SERVER_PORT,
    ClientEntry,
    Settings,
    SingBoxConfig,
    atomic_write_json,
    backup,
    clients_from_config_users,
    default_clients,
    default_config,
    find_vless_inbound,
    load_settings,
    now_ctime,
    read_json,
    users_from_clients_table,
)
from .share import (
    build_outer_share_config,
    make_qr_chunks,
    qcompress,
    vpn_url_from_qcompressed,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

OSC52_MAX_PAYLOAD = 120000
KEY_BYTE_MAX = 256  # upper bound for single-byte key values
CTRL_A = 1
CTRL_E = 5
CTRL_K = 11
CTRL_U = 21
MARK_BOLD_ON = "\x01"  # simple inline markup for emphasis
MARK_BOLD_OFF = "\x02"

with contextlib.suppress(AttributeError):
    curses.set_escdelay(25)

CATPPUCCIN_MOCHA = {
    "rosewater": "f5e0dc",
    "lavender": "b4befe",
    "peach": "fab387",
    "text": "cdd6f4",
    "surface1": "45475a",
    "panel_bg": "313244",
    "input_bg": "585b70",
    "cursor_bg": "f38ba8",
    "cursor_fg": "11111b",
    "base": "1e1e2e",
}


@dataclass(frozen=True)
class CommandBinding:
    """Registry entry describing a keyboard shortcut and its behavior."""

    display: str
    keys: tuple[int, ...]
    handler: Callable[[], bool]
    show_in_help: bool = True


@dataclass
class ModalSpec:
    """Declarative description of a simple modal window."""

    header: str
    body_lines: Sequence[str]
    footer: str | None = None


def docker_check_config(config_path: Path, image: str) -> tuple[bool, str]:
    """Validate sing-box config using Docker container.

    Args:
        config_path (Path): Path to the config.json file to validate.
        image (str): Docker image used for running the sing-box check.

    Returns:
        tuple[bool, str]: (success status, output message).
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{config_path}:/etc/sing-box/config.json:ro",
        image,
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
        self.config_path = Path(self.settings.config_path)
        self.table_path = Path(self.settings.clients_table)
        self.config_file = self.config_path
        self.table_file = self.table_path
        self.flow = DEFAULT_FLOW

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
        self.styles = self._init_styles()
        with contextlib.suppress(curses.error):
            self.stdscr.bkgd(" ", self.styles.get("background", 0))

    # ---------- UI helpers ----------
    def draw(self) -> None:
        """Draw the complete TUI interface including title, help text, client list, and status."""
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        content_width = max(10, w - 1)

        self._draw_title(w)
        list_start_y = self._draw_help_section(start_y=1, width=w)
        list_start_y = self._draw_table_header(list_start_y, w)

        footer_path_rows = self._wrap_segments(
            [
                f"clients table: {self.table_path}",
                f"config: {self.config_path}",
            ],
            content_width,
        )
        reserved_bottom = len(footer_path_rows) + 1
        list_height = max(1, h - list_start_y - reserved_bottom)

        self._draw_client_rows(list_start_y, list_height, w)
        self._draw_footer(h - reserved_bottom, footer_path_rows, w)
        self.stdscr.refresh()

    def _draw_title(self, width: int) -> None:
        dirty_mark = " · MODIFIED" if self.dirty else ""
        title = f"Manage Sing-Box Users{dirty_mark}"
        self.stdscr.addnstr(0, 0, title, width - 1, self.styles["title"])

    def _build_help_rows(self) -> list[Sequence[str]]:
        help_segments = [
            cmd.display for cmd in self.command_bindings if cmd.show_in_help
        ]
        midpoint = (len(help_segments) + 1) // 2
        return [
            row for row in (help_segments[:midpoint], help_segments[midpoint:]) if row
        ]

    def _draw_help_section(self, start_y: int, width: int) -> int:
        rows = self._build_help_rows()
        for idx, row in enumerate(rows):
            self._draw_help_row(start_y + idx, row, width)
        return start_y + len(rows)

    def _draw_help_row(self, y: int, segments: Sequence[str], width: int) -> None:
        col = 0

        def write(text: str, attr: int) -> None:
            nonlocal col
            if not text or col >= width - 1:
                return
            space = width - 1 - col
            self.stdscr.addnstr(y, col, text, space, attr)
            col += min(len(text), space)

        for idx, segment in enumerate(segments):
            if idx:
                write(" · ", self.styles["help_sep"])
            key_part, _, desc_part = segment.partition(" ")
            write(key_part, self.styles["help_key"])
            if desc_part:
                write(f" {desc_part}", self.styles["help_dim"])

    def _draw_table_header(self, start_y: int, width: int) -> int:
        self.stdscr.addnstr(
            start_y,
            0,
            f"{'#':>3}  {'UUID':36}  {'CREATED':24}  NAME",
            width - 1,
            self.styles["table_header"],
        )
        return start_y + 1

    def _draw_client_rows(self, start_y: int, list_height: int, width: int) -> None:
        list_height = max(1, list_height)
        max_start = max(0, len(self.clients) - list_height)
        self.view_top = min(self.view_top, max_start)
        if self.cursor < self.view_top:
            self.view_top = self.cursor
        elif self.cursor >= self.view_top + list_height:
            self.view_top = self.cursor - list_height + 1
        start = self.view_top
        rows = self.clients[start : start + list_height]
        for idx, client in enumerate(rows):
            i = start + idx
            line = self._format_client_row(i, client)
            attr = (
                self.styles["row_selected"] if i == self.cursor else self.styles["row"]
            )
            self.stdscr.addnstr(start_y + idx, 0, line, width - 1, attr)

    def _visible_length(self, text: str) -> int:
        """Return printable width of text, ignoring inline markup tokens."""

        return sum(ch not in (MARK_BOLD_ON, MARK_BOLD_OFF) for ch in text)

    def _addnstr_with_markup(
        self,
        window: curses.window,
        y: int,
        x: int,
        text: str,
        max_width: int,
    ) -> None:
        """Render text honoring inline bold markers within modal strings."""

        col = 0
        attr = 0
        for ch in text:
            if col >= max_width:
                break
            if ch == MARK_BOLD_ON:
                attr = curses.A_BOLD
                continue
            if ch == MARK_BOLD_OFF:
                attr = 0
                continue
            window.addnstr(y, x + col, ch, 1, attr)
            col += 1

    def _format_client_row(self, index: int, client: ClientEntry) -> str:
        uid = (client.get("clientId") or "")[:36]
        user_data = client.get("userData") or {}
        name = user_data.get("clientName", "")
        created_raw = user_data.get("creationDate", "")
        created = created_raw[:24]
        row_number = index + 1
        return f"{row_number:>3}  {uid:36}  {created:24}  {name}"

    def _draw_footer(self, status_y: int, path_rows: Sequence[str], width: int) -> None:
        self.stdscr.addnstr(
            status_y,
            0,
            (self.message or "")[: width - 1],
            width - 1,
            self.styles["status"],
        )
        for idx, text in enumerate(path_rows):
            self.stdscr.addnstr(
                status_y + 1 + idx, 0, text, width - 1, self.styles["path"]
            )

    def _wrap_segments(
        self, segments: Sequence[str], width: int, separator: str = " · "
    ) -> list[str]:
        rows: list[str] = []
        current = ""
        for segment in segments:
            candidate = segment if not current else f"{current}{separator}{segment}"
            if len(candidate) > width and current:
                rows.append(current)
                current = segment
            else:
                current = candidate
        if current:
            rows.append(current)
        return rows

    def _init_styles(self) -> dict[str, int]:
        """Prepare Catppuccin Mocha-inspired colors and attributes."""

        styles: dict[str, int] = {
            "title": curses.A_BOLD,
            "help_dim": curses.A_DIM,
            "help_key": curses.A_DIM | curses.A_BOLD,
            "help_sep": curses.A_DIM,
            "table_header": curses.A_BOLD | curses.A_UNDERLINE,
            "row": 0,
            "row_selected": curses.A_REVERSE | curses.A_BOLD,
            "input_field": curses.A_REVERSE,
            "input_cursor": curses.A_REVERSE | curses.A_BOLD,
            "status": curses.A_DIM | curses.A_BOLD,
            "path": curses.A_DIM,
            "background": 0,
            "modal_window": 0,
        }

        if not curses.has_colors():
            return styles

        curses.start_color()
        curses.use_default_colors()

        def hex_to_curses_rgb(code: str) -> tuple[int, int, int]:
            code = code.lstrip("#")
            r = int(code[0:2], 16)
            g = int(code[2:4], 16)
            b = int(code[4:6], 16)
            r_scaled = round(r / 255 * 1000)
            g_scaled = round(g / 255 * 1000)
            b_scaled = round(b / 255 * 1000)
            return r_scaled, g_scaled, b_scaled

        def register_catppuccin_colors() -> dict[str, int]:
            if not curses.can_change_color():
                return {}
            base_index = 16
            required_slots = base_index + len(CATPPUCCIN_MOCHA)
            if required_slots > curses.COLORS:
                return {}
            assigned: dict[str, int] = {}
            for offset, (name, code) in enumerate(CATPPUCCIN_MOCHA.items()):
                color_id = base_index + offset
                r, g, b = hex_to_curses_rgb(code)
                try:
                    curses.init_color(color_id, r, g, b)
                except curses.error:
                    assigned.clear()
                    break
                assigned[name] = color_id
            return assigned

        custom_colors = register_catppuccin_colors()

        def color(name: str, fallback: int) -> int:
            return custom_colors.get(name, fallback)

        def make_pair(idx: int, fg: int, bg: int = -1) -> int:
            with contextlib.suppress(curses.error):
                curses.init_pair(idx, fg, bg)
            return curses.color_pair(idx)

        base_color = color("base", curses.COLOR_BLACK)
        rosewater = make_pair(1, color("rosewater", curses.COLOR_MAGENTA), base_color)
        lavender = make_pair(2, color("lavender", curses.COLOR_CYAN), base_color)
        peach = make_pair(3, color("peach", curses.COLOR_YELLOW), base_color)
        text = make_pair(4, color("text", curses.COLOR_WHITE), base_color)
        focus = make_pair(
            5, color("text", curses.COLOR_WHITE), color("surface1", curses.COLOR_BLUE)
        )
        panel = make_pair(
            6, color("text", curses.COLOR_WHITE), color("panel_bg", curses.COLOR_BLUE)
        )
        cursor_accent = make_pair(
            7,
            color("cursor_fg", curses.COLOR_BLACK),
            color("cursor_bg", curses.COLOR_MAGENTA),
        )
        background_attr = make_pair(8, -1, base_color)
        input_field_attr = make_pair(
            9,
            color("text", curses.COLOR_WHITE),
            color("input_bg", curses.COLOR_BLUE),
        )

        styles.update(
            {
                "title": rosewater | curses.A_BOLD,
                "help_dim": lavender | curses.A_DIM,
                "help_key": peach | curses.A_BOLD,
                "help_sep": lavender | curses.A_DIM,
                "table_header": rosewater | curses.A_BOLD,
                "row": text,
                "row_selected": focus | curses.A_BOLD,
                "input_field": input_field_attr,
                "input_cursor": cursor_accent | curses.A_BOLD,
                "status": peach | curses.A_BOLD,
                "path": lavender | curses.A_DIM,
                "background": background_attr,
                "modal_window": panel,
            }
        )
        styles["path"] = styles["help_dim"]
        return styles

    def _apply_modal_background(self, window: curses.window) -> None:
        attr = self.styles.get("modal_window")
        if not attr:
            return
        with contextlib.suppress(curses.error):
            window.bkgd(" ", attr)

    def _prepare_modal_interaction(self) -> None:
        self.stdscr.nodelay(False)
        curses.noecho()
        with contextlib.suppress(curses.error):
            curses.curs_set(0)

    def _spawn_modal_window(
        self,
        desired_width: int,
        desired_height: int,
        *,
        min_width: int = 10,
        min_height: int = 5,
    ) -> tuple[curses.window, int, int]:
        h, w = self.stdscr.getmaxyx()
        max_width = max(min_width, w - 2)
        max_height = max(min_height, h - 2)
        width = min(max(desired_width, min_width), max_width)
        height = min(max(desired_height, min_height), max_height)
        top = max(0, (h - height) // 2)
        left = max(0, (w - width) // 2)
        win = curses.newwin(height, width, top, left)
        win.keypad(True)
        self._apply_modal_background(win)
        return win, height, width

    @contextlib.contextmanager
    def _modal_window(
        self,
        desired_width: int,
        desired_height: int,
        *,
        min_width: int = 10,
        min_height: int = 5,
    ) -> Iterator[tuple[curses.window, int, int]]:
        """Context manager that prepares, creates, and tears down a modal window."""
        self._prepare_modal_interaction()
        win, height, width = self._spawn_modal_window(
            desired_width,
            desired_height,
            min_width=min_width,
            min_height=min_height,
        )
        try:
            yield win, height, width
        finally:
            win.erase()
            win.refresh()
            del win
            with contextlib.suppress(curses.error):
                curses.curs_set(0)
            self.stdscr.touchwin()
            self.stdscr.refresh()

    @contextlib.contextmanager
    def _temporary_terminal_mode(self) -> Iterator[None]:
        """Suspend curses to let external commands draw directly to the terminal."""

        curses.def_prog_mode()
        curses.endwin()
        try:
            yield
        finally:
            curses.reset_prog_mode()
            self.stdscr.touchwin()
            self.stdscr.refresh()

    def _modal_loop(
        self,
        window: curses.window,
        redraw: Callable[[], None],
        handler: Callable[[int], bool],
    ) -> None:
        """Run a modal interaction loop until handler requests exit."""

        while True:
            redraw()
            try:
                ch = window.getch()
            except KeyboardInterrupt:
                ch = 3  # treat as Ctrl-C
            if handler(ch):
                break

    def _render_modal_spec(
        self,
        window: curses.window,
        width: int,
        height: int,
        spec: ModalSpec,
    ) -> None:
        window.erase()
        window.border()
        self._addnstr_with_markup(window, 1, 2, spec.header, width - 4)
        for idx, line in enumerate(spec.body_lines):
            y = 2 + idx
            if y >= height - 2:
                break
            self._addnstr_with_markup(
                window,
                y,
                2,
                line,
                width - 4,
            )
        if spec.footer:
            # Footer intentionally renders raw text — no markup support.
            window.addnstr(
                height - 2, 2, spec.footer[: width - 4], width - 4, curses.A_DIM
            )
        window.refresh()

    def _draw_button_row(
        self,
        window: curses.window,
        y: int,
        width: int,
        tokens: Sequence[str],
        selected: int,
    ) -> None:
        x = 2
        for idx, token in enumerate(tokens):
            attr = curses.A_REVERSE if idx == selected else curses.A_BOLD
            space = max(0, width - 4 - x)
            if space <= 0:
                break
            window.addnstr(y, x, token, space, attr)
            x += len(token) + 1
            if x >= width - 2:
                break

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
        """Prompt user for text input inside a centered modal window.

        Args:
            prompt_text: Message displayed at the top of the modal.
            initial_text: Optional text to pre-fill into the input buffer.
        """
        max_chars = 512
        seed = (initial_text or "")[:max_chars]
        value: list[str] = list(seed)
        desired_width = max(len(prompt_text) + 10, 30)
        result: str | None = None

        with self._modal_window(
            desired_width,
            7,
            min_width=30,
            min_height=7,
        ) as (win, height, width):
            cursor = len(value)
            scroll = 0

            def build_instructions(max_width: int) -> str:
                segments = [
                    "Esc cancel",
                    "Enter accept",
                    "←→ move",
                    "Ctrl-A/E/U/K",
                ]
                while segments:
                    text = " · ".join(segments)
                    if len(text) <= max_width:
                        return text
                    segments.pop()
                return "Esc cancel"

            instructions = build_instructions(width - 4)

            def redraw_modal(window: curses.window, buffer: list[str]) -> None:
                nonlocal scroll
                window.erase()
                window.border()
                window.addnstr(1, 2, prompt_text[: width - 4], width - 4)
                text = "".join(buffer)
                input_width = width - 4
                if cursor < scroll:
                    scroll = cursor
                elif cursor > scroll + input_width:
                    scroll = cursor - input_width
                scroll = max(0, min(scroll, max(0, len(text) - input_width)))
                slice_text = text[scroll : scroll + input_width]
                padded = slice_text.ljust(input_width)
                input_attr = self.styles.get("input_field", curses.A_REVERSE)
                window.addnstr(3, 2, padded, input_width, input_attr)
                window.addnstr(4, 2, " " * input_width, input_width)
                info = instructions[: width - 4]
                window.addnstr(height - 2, 2, " " * (width - 4), width - 4)
                window.addnstr(height - 2, 2, info, width - 4, curses.A_DIM)
                cursor_rel = max(0, min(cursor - scroll, input_width - 1))
                cursor_char = padded[cursor_rel] if cursor_rel < len(padded) else " "
                cursor_attr = self.styles.get(
                    "input_cursor", input_attr | curses.A_BOLD
                )
                window.addch(3, 2 + cursor_rel, cursor_char or " ", cursor_attr)
                window.refresh()

            def redraw() -> None:
                redraw_modal(win, value)

            def handle(ch: int) -> bool:
                nonlocal cursor, result
                if ch in (curses.KEY_ENTER, 10, 13):
                    result = None if not value else "".join(value)
                    return True
                if ch in (27, 3):
                    result = None
                    return True
                if ch in (curses.KEY_LEFT, ord("h")):
                    cursor = max(0, cursor - 1)
                    return False
                if ch in (curses.KEY_RIGHT, ord("l")):
                    cursor = min(len(value), cursor + 1)
                    return False
                if ch in (curses.KEY_HOME, CTRL_A):
                    cursor = 0
                    return False
                if ch in (curses.KEY_END, CTRL_E):
                    cursor = len(value)
                    return False
                if ch == CTRL_U:
                    if cursor > 0:
                        del value[:cursor]
                        cursor = 0
                    return False
                if ch == CTRL_K:
                    if cursor < len(value):
                        del value[cursor:]
                    return False
                if ch in (curses.KEY_BACKSPACE, 127, 8):
                    if cursor > 0:
                        cursor -= 1
                        value.pop(cursor)
                    return False
                if ch == curses.KEY_DC:
                    if cursor < len(value):
                        value.pop(cursor)
                    return False
                if 0 <= ch < KEY_BYTE_MAX and len(value) < max_chars:
                    value.insert(cursor, chr(ch))
                    cursor += 1
                return False

            self._modal_loop(win, redraw, handle)

        return result

    def prompt_choice(self, prompt_text: str, choices: str) -> str | None:
        """Prompt for a single-character choice inside a modal window."""
        text = f"{prompt_text}"
        hint = f"[{'/'.join(choices)}]"
        spec = ModalSpec(header=text, body_lines=[hint], footer="Esc cancel")
        inner_width = max(self._visible_length(text) + len(hint) + 6, 30)
        result: str | None = None

        with self._modal_window(
            inner_width,
            5,
            min_width=30,
            min_height=5,
        ) as (win, height, width):

            def redraw_choice() -> None:
                self._render_modal_spec(win, width, height, spec)

            def redraw() -> None:
                redraw_choice()

            def handle(ch: int) -> bool:
                nonlocal result
                if ch in (3, 27):
                    result = None
                    return True
                if 0 <= ch < KEY_BYTE_MAX:
                    c = chr(ch).lower()
                    if c in choices:
                        result = c
                        return True
                return False

            self._modal_loop(win, redraw, handle)
        return result

    def prompt_buttons(
        self, prompt_text: str, buttons: Sequence[tuple[str, str]]
    ) -> str | None:
        """Prompt inside a modal window that renders selectable buttons."""
        if not buttons:
            return None

        prompt_lines = prompt_text.splitlines() or [""]
        header_line = prompt_lines[0]
        body_lines = ["", *prompt_lines[1:]]
        if body_lines[-1] != "":
            body_lines.append("")
        button_row_y = 2 + len(body_lines)
        desired_height = max(5, len(body_lines) + 4)
        button_tokens = [f"[ {label} ]" for label, _ in buttons]
        buttons_line = " ".join(button_tokens)
        longest_prompt_line = max(self._visible_length(line) for line in prompt_lines)
        inner_width = max(
            longest_prompt_line + 4,
            len(buttons_line) + 6,
            36,
        )
        result: str | None = None
        selected = 0

        with self._modal_window(
            inner_width,
            desired_height,
            min_width=36,
            min_height=5,
        ) as (win, height, width):

            def redraw_buttons() -> None:
                self._render_modal_spec(
                    win,
                    width,
                    height,
                    ModalSpec(
                        header=header_line,
                        body_lines=body_lines,
                    ),
                )
                self._draw_button_row(
                    win,
                    button_row_y,
                    width,
                    button_tokens,
                    selected,
                )

            def redraw() -> None:
                redraw_buttons()

            def handle(ch: int) -> bool:
                nonlocal selected, result
                if ch in (27, 3):
                    result = None
                    return True
                if ch in (curses.KEY_LEFT, ord("h")):
                    selected = (selected - 1) % len(buttons)
                    return False
                if ch in (curses.KEY_RIGHT, ord("l")):
                    selected = (selected + 1) % len(buttons)
                    return False
                if ch in (curses.KEY_ENTER, 10, 13, ord(" ")):
                    result = buttons[selected][1]
                    return True
                return False

            self._modal_loop(win, redraw, handle)

        return result

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
        ok, out = docker_check_config(self.config_path, self.settings.docker_image)
        tail = (out or "").splitlines()[-1] if out else ""
        self.message = f"check={'OK' if ok else 'FAIL'} {tail}"

    def do_restart(self) -> None:
        """Restart the sing-box Docker container."""
        ok, out = docker_restart(self.settings.container)
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
            vpn_url, qr_payloads = self._build_share_payload(client_id)
        except ValueError as e:
            self.message = str(e)
            return
        show_qr, status = self._show_share_modal(vpn_url)
        self.message = status
        if show_qr:
            self._display_qr_series(vpn_url, qr_payloads)

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
        dns1 = self.settings.share_dns1
        dns2 = self.settings.share_dns2
        container = self.settings.container
        server_port = self.settings.server_port
        if not MIN_SERVER_PORT <= server_port <= MAX_SERVER_PORT:
            raise ValueError(
                f"Server_port must be between {MIN_SERVER_PORT} and {MAX_SERVER_PORT}."
            )
        server_name = self.settings.server_sni or DEFAULT_SERVER_SNI
        outer = build_outer_share_config(
            server_ip,
            client_id,
            server_pubkey,
            server_short_id,
            description=self.settings.share_description,
            dns1=dns1,
            dns2=dns2,
            container=container,
            port=server_port,
            server_name=server_name,
        )
        outer_json = json.dumps(outer, indent=4, ensure_ascii=False) + "\n"
        qc = qcompress(outer_json.encode("utf-8"), level=8)
        url = vpn_url_from_qcompressed(qc)
        try:
            qr_payloads = make_qr_chunks(qc)
        except ValueError as e:
            raise ValueError(f"Failed to split QR chunks: {e}") from e
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
        except OSError as exc:
            raise RuntimeError(f"clipboard unavailable ({exc})") from exc

    def _show_share_modal(self, vpn_url: str) -> tuple[bool, str]:
        """Display share actions and return (show_qr, status_message)."""

        header = "Client config ready."
        status_line = "Cancelled."

        while True:
            result = self.prompt_buttons(
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

    def _display_qr_series(self, vpn_url: str, payloads: Sequence[str]) -> None:
        if not payloads:
            self.message = "No QR payloads to display."
            return
        imgcat = shutil.which("imgcat")
        if not imgcat:
            self.message = "imgcat command not found in PATH."
            return

        def build_png(payload: str) -> bytes:
            qr = qrcode.QRCode(
                error_correction=ERROR_CORRECT_L,
                box_size=6,
                border=2,
            )
            qr.add_data(payload)
            qr.make(fit=True)
            img = qr.make_image(
                image_factory=PilImage,
                fill_color="black",
                back_color="white",
            )
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        with self._temporary_terminal_mode():
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
        self.message = "QR display finished."

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
        help=f"Path to sing-box config.json (default: {DEFAULT_CONFIG})",
    )
    ap.add_argument(
        "--clients-table",
        type=Path,
        default=None,
        metavar="PATH",
        dest="clients_table",
        help=f"Path to clientsTable.json (default: {DEFAULT_TABLE})",
    )
    args = ap.parse_args()

    settings = load_settings(DEFAULT_SETTINGS_PATH)
    if args.config is not None:
        settings = replace(settings, config_path=args.config)
    if args.clients_table is not None:
        settings = replace(settings, clients_table=args.clients_table)

    curses.wrapper(lambda stdscr: App(stdscr, settings).run())


if __name__ == "__main__":
    main()
