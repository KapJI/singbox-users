"""Modal dialog helpers for the curses-based UI."""

from __future__ import annotations

import contextlib
import curses
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

MARK_BOLD_ON = "\x01"
MARK_BOLD_OFF = "\x02"
CTRL_A = 1
CTRL_E = 5
CTRL_K = 11
CTRL_U = 21
KEY_BYTE_MAX = 256


@dataclass
class ModalSpec:
    """Declarative description of a simple modal window."""

    header: str
    body_lines: Sequence[str]
    footer: str | None = None


class ModalManager:
    """Render and handle modal dialogs that suspend the main UI."""

    def __init__(self, stdscr: curses.window, styles: dict[str, int]) -> None:
        """Store references to the curses window and style palette."""

        self.stdscr = stdscr
        self.styles = styles

    def prompt_line(
        self, prompt_text: str, initial_text: str | None = None
    ) -> str | None:
        """Return free-form user input gathered via a modal text field."""

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
                if ch == curses.KEY_LEFT:
                    cursor = max(0, cursor - 1)
                    return False
                if ch == curses.KEY_RIGHT:
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
        """Return the first matching character choice or None when cancelled."""

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
        """Return the value of the activated button or None if cancelled."""

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

    # ---- helpers ----
    def _visible_length(self, text: str) -> int:
        return sum(ch not in (MARK_BOLD_ON, MARK_BOLD_OFF) for ch in text)

    def _addnstr_with_markup(
        self,
        window: curses.window,
        y: int,
        x: int,
        text: str,
        max_width: int,
    ) -> None:
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

    def _modal_loop(
        self,
        window: curses.window,
        redraw: Callable[[], None],
        handler: Callable[[int], bool],
    ) -> None:
        while True:
            redraw()
            try:
                ch = window.getch()
            except KeyboardInterrupt:
                ch = 3
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
            self._addnstr_with_markup(window, y, 2, line, width - 4)
        if spec.footer:
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
