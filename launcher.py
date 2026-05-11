"""PyInstaller entry point for StoryViz.

This wrapper exists so we can:

1. Replace ``sys.stdout`` / ``sys.stderr`` when they are ``None`` (which
   is the case under PyInstaller ``--windowed`` on Windows). If any
   import-time ``print(...)`` or ``logging`` call tries to use a
   ``None`` stream, the whole process crashes with no useful diagnostic.
2. Wrap the entire startup in a top-level ``try/except`` so that any
   crash (including those during module imports) is written to
   ``storyviz-error.log`` next to the executable and shown in a Tk
   error dialog if Tk is available.

This file is only ever the PyInstaller entry point; running it directly
with a normal Python interpreter (where ``sys.stdout`` is already a
real TTY) is also fine — the redirects are no-ops in that case.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _exe_dir() -> Path:
    """Directory the .exe lives in (or the CWD when not frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _redirect_null_streams() -> None:
    """If sys.stdout / sys.stderr are None (PyInstaller --windowed),
    redirect them to a log file next to the exe so library code that
    does ``print(...)`` or ``logging`` doesn't crash the interpreter."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    target_path = _exe_dir() / "storyviz-stderr.log"
    try:
        stream = open(target_path, "w", encoding="utf-8", buffering=1)
    except OSError:
        # Last resort: drop everything into the null device. The app may
        # still work; we just can't capture diagnostics.
        stream = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _show_crash_dialog(traceback_text: str, log_path: Path) -> None:
    """Best-effort error popup. Swallow all errors — we are already in
    the crash path and don't want to mask the original traceback."""
    try:
        from tkinter import Tk, messagebox as mb

        root = Tk()
        root.withdraw()
        mb.showerror(
            "StoryViz — startup crash",
            f"StoryViz could not start.\n\n"
            f"Traceback was written to:\n{log_path}\n\n"
            f"{traceback_text}",
        )
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    _redirect_null_streams()
    log_path = _exe_dir() / "storyviz-error.log"
    # Wipe stale log from previous runs so users only see the latest.
    try:
        log_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    try:
        # Import here (NOT at module top) so any ImportError from the
        # heavyweight deps still gets caught by this try/except.
        from app import main as _app_main

        _app_main()
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        try:
            log_path.write_text(tb, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        _show_crash_dialog(tb, log_path)
        # Re-raise so a --console build still prints to the terminal.
        raise


if __name__ == "__main__":
    main()
