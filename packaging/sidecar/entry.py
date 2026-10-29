"""Frozen entry point used by the desktop bundle.

Only environment parsing and uvicorn startup live here; application settings
remain in app/config.py so the source build and the frozen build behave alike.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    if "--version" in sys.argv:
        from importlib.metadata import version

        print(version("learning-space"))
        return 0
    import uvicorn

    host = os.environ.get("LS_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("LS_PORT", "8765"))
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level=os.environ.get("LS_LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
