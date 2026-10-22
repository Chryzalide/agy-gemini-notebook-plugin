"""Lightweight HTTP server for the Antigravity 2 Gemini Notebook Sidecar Panel."""

from __future__ import annotations

import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

SIDECAR_DIR = Path(__file__).resolve().parent


class SidecarRequestHandler(SimpleHTTPRequestHandler):
    """Serves `ui.html` and static assets for the Aux Pane sidecar."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SIDECAR_DIR), **kwargs)


def main() -> None:
    port = int(os.environ.get("PORT", "8765"))
    server = HTTPServer(("127.0.0.1", port), SidecarRequestHandler)
    print(f"Gemini Notebook Sidecar UI listening on http://127.0.0.1:{port}/ui.html", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
