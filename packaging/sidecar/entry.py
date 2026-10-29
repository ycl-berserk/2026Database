"""Frozen entry point: delegate to the desktop runtime CLI.

The packaged binary behaves like ``ls-server``: init-env / bootstrap / serve /
backup / restore / doctor / smoke.
"""

from __future__ import annotations

import sys


def main() -> int:
    if "--version" in sys.argv:
        from importlib.metadata import version

        print(version("learning-space"))
        return 0
    from app.desktop import main as desktop_main

    return desktop_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
