"""Rendering helpers for the curses sing-box TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import curses
    from pathlib import Path

    from singbox_users.main import CommandBinding
    from singbox_users.singbox_config import ClientEntry


@dataclass
class MainView:
    """Render the primary screen that lists clients and shortcuts."""

    stdscr: curses.window
    styles: dict[str, int]
    table_path: Path
    config_path: Path

    def draw(
        self,
        bindings: tuple[CommandBinding, ...],
        clients: list[ClientEntry],
        cursor: int,
        view_top: int,
        message: str,
        dirty: bool,
    ) -> int:
        """Render the full UI and return the adjusted view_top."""

        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        content_width = max(10, w - 1)
        self._draw_title(w, dirty)
        list_start_y = self._draw_help_section(1, w, bindings)
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
        view_top = self._draw_client_rows(
            list_start_y,
            list_height,
            w,
            clients,
            cursor,
            view_top,
        )
        self._draw_footer(h - reserved_bottom, footer_path_rows, w, message)
        self.stdscr.refresh()
        return view_top

    def _draw_title(self, width: int, dirty: bool) -> None:
        dirty_mark = " · MODIFIED" if dirty else ""
        title = f"Manage Sing-Box Users{dirty_mark}"
        self.stdscr.addnstr(0, 0, title, width - 1, self.styles["title"])

    def _build_help_rows(self, bindings: tuple[CommandBinding, ...]) -> list[list[str]]:
        help_segments = [cmd.display for cmd in bindings if cmd.show_in_help]
        midpoint = (len(help_segments) + 1) // 2
        return [
            row for row in (help_segments[:midpoint], help_segments[midpoint:]) if row
        ]

    def _draw_help_section(
        self, start_y: int, width: int, bindings: tuple[CommandBinding, ...]
    ) -> int:
        rows = self._build_help_rows(bindings)
        for idx, row in enumerate(rows):
            self._draw_help_row(start_y + idx, row, width)
        return start_y + len(rows)

    def _draw_help_row(
        self,
        y: int,
        segments: list[str],
        width: int,
    ) -> None:
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

    def _draw_client_rows(
        self,
        start_y: int,
        list_height: int,
        width: int,
        clients: list[ClientEntry],
        cursor: int,
        view_top: int,
    ) -> int:
        list_height = max(1, list_height)
        max_start = max(0, len(clients) - list_height)
        view_top = min(view_top, max_start)
        if cursor < view_top:
            view_top = cursor
        elif cursor >= view_top + list_height:
            view_top = cursor - list_height + 1
        start = view_top
        rows = clients[start : start + list_height]
        for idx, client in enumerate(rows):
            i = start + idx
            line = self._format_client_row(i, client)
            attr = self.styles["row_selected"] if i == cursor else self.styles["row"]
            self.stdscr.addnstr(start_y + idx, 0, line, width - 1, attr)
        return view_top

    def _format_client_row(self, index: int, client: ClientEntry) -> str:
        uid = (client.get("clientId") or "")[:36]
        user_data = client.get("userData") or {}
        name = user_data.get("clientName", "")
        created_raw = user_data.get("creationDate", "")
        created = created_raw[:24]
        row_number = index + 1
        return f"{row_number:>3}  {uid:36}  {created:24}  {name}"

    def _draw_footer(
        self,
        status_y: int,
        path_rows: list[str],
        width: int,
        message: str,
    ) -> None:
        self.stdscr.addnstr(
            status_y,
            0,
            (message or "")[: width - 1],
            width - 1,
            self.styles["status"],
        )
        for idx, text in enumerate(path_rows):
            self.stdscr.addnstr(
                status_y + 1 + idx, 0, text, width - 1, self.styles["path"]
            )

    def _wrap_segments(
        self, segments: list[str], width: int, separator: str = " · "
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
