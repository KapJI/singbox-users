"""Color and style helpers for the curses TUI."""

from __future__ import annotations

import contextlib
import curses

CATPPUCCIN_MOCHA = {
    "rosewater": "#f5e0dc",
    "lavender": "#b4befe",
    "peach": "#fab387",
    "text": "#cdd6f4",
    "surface1": "#45475a",
    "panel_bg": "#313244",
    "input_bg": "#585b70",
    "cursor_bg": "#f38ba8",
    "cursor_fg": "#11111b",
    "base": "#1e1e2e",
}


def _hex_to_curses_rgb(code: str) -> tuple[int, int, int]:
    code = code.lstrip("#")
    r = int(code[0:2], 16)
    g = int(code[2:4], 16)
    b = int(code[4:6], 16)
    r_scaled = round(r / 255 * 1000)
    g_scaled = round(g / 255 * 1000)
    b_scaled = round(b / 255 * 1000)
    return r_scaled, g_scaled, b_scaled


def init_styles() -> dict[str, int]:
    """Return a dict of curses attribute styles (Catppuccin Mocha inspired)."""

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

    try:
        curses.start_color()
        curses.use_default_colors()
    except curses.error:
        return styles

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
            r, g, b = _hex_to_curses_rgb(code)
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
