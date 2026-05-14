#!/usr/bin/env python3
"""Launch `langgraph dev` for Studio with sensible local defaults.

Two adjustments vs. `langgraph dev` directly:

  * `--allow-blocking` is always passed. Our `studio_graphs.py` factories
    do a synchronous SQLite read via `template_store.get_row()` when
    Studio asks them to materialise a graph; `blockbuster` (bundled with
    `langgraph dev`) trips on that without this flag.

  * The `watchfiles.main` logger is dialled down to WARNING. `watchfiles`
    is the file-system watcher `langgraph dev` uses for hot reload. It
    logs every detected change at INFO, including the SQLite WAL files
    under `backend/data/` that get touched every few seconds by the app.
    Those events never actually trigger a reload (only Python source
    changes do) — they're pure noise. Suppressing the logger keeps the
    reload behaviour intact and the console quiet.

Any extra CLI args are forwarded to `langgraph dev` unchanged.
"""

from __future__ import annotations

import logging
import sys

logging.getLogger("watchfiles").setLevel(logging.WARNING)
logging.getLogger("watchfiles.main").setLevel(logging.WARNING)

from langgraph_cli.cli import cli  # noqa: E402 — must follow logger config


def main() -> None:
    sys.argv = [sys.argv[0], "dev", "--allow-blocking", *sys.argv[1:]]
    cli()


if __name__ == "__main__":
    main()
