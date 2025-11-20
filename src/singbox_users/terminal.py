"""Utility helpers for temporarily suspending curses rendering."""

from __future__ import annotations

import contextlib
import curses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


@contextlib.contextmanager
def suspend_curses(stdscr: curses.window) -> Generator[None]:
    """Allow external programs to draw directly and then resume curses."""

    curses.def_prog_mode()
    curses.endwin()
    try:
        yield
    finally:
        curses.reset_prog_mode()
        stdscr.touchwin()
        stdscr.refresh()
