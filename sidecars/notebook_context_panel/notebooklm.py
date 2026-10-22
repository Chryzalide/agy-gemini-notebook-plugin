"""Fallback launcher shim when `/usr/bin/python3 -m notebooklm` is invoked from the sidecar directory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VENV_BIN = _REPO_ROOT / ".venv" / "bin"


def _exec_venv_notebooklm() -> None:
    env = os.environ.copy()
    pw_browsers = _REPO_ROOT / ".venv" / "playwright-browsers"
    if pw_browsers.is_dir() and not env.get("PLAYWRIGHT_BROWSERS_PATH"):
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(pw_browsers)

    cli_bin = _VENV_BIN / "notebooklm"
    if cli_bin.is_file():
        os.execve(str(cli_bin), [str(cli_bin), *sys.argv[1:]], env)

    venv_py = _VENV_BIN / "python"
    if venv_py.is_file():
        os.chdir(_REPO_ROOT)
        os.execve(str(venv_py), [str(venv_py), "-m", "notebooklm", *sys.argv[1:]], env)

    raise SystemExit("notebooklm-py is not installed in .venv. Run `./scripts/setup.sh`.")


if __name__ == "__main__":
    _exec_venv_notebooklm()
