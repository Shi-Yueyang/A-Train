"""PyInstaller entry point: runs the CLI through the installed package so
``a_train``'s relative imports resolve inside the frozen bundle."""

from a_train.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
