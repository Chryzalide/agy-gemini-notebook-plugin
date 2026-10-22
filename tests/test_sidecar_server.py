"""Unit and HTTP integration tests for `sidecars/notebook_context_panel/server.py` and `ui.html`."""

from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "sidecars" / "notebook_context_panel" / "server.py"
_DIRECT_OPENER = build_opener(ProxyHandler({}))


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("sidecar_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _http_json(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with _DIRECT_OPENER.open(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_sidecar_http_lifecycle_and_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIDECAR_QUIET", "1")
    (tmp_path / "README.md").write_text("# Demo Project\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Agent Rules\n", encoding="utf-8")

    server_mod = _load_server_module()

    async def fake_notebook_list(
        page_size: int = 50,
        query: str | None = None,
        engine: str = "auto",
        workspace_dir: str | None = None,
    ) -> dict[str, Any]:
        return {
            "engine": "rpc_bridge",
            "count": 2,
            "notebooks": [
                {
                    "name": "projects/123/locations/global/notebooks/nb-alpha",
                    "title": "Alpha Architecture",
                    "sourcesCount": 3,
                },
                {
                    "notebookId": "nb-beta",
                    "title": "Beta Research",
                    "emoji": "🧪",
                    "sourcesCount": 1,
                },
            ],
        }

    async def fake_notebook_get(
        notebook_id: str | None = None,
        engine: str = "auto",
        workspace_dir: str | None = None,
    ) -> dict[str, Any]:
        return {
            "notebookId": notebook_id or "nb-alpha",
            "title": "Alpha Architecture",
            "summary": "Core architecture specification and ADRs.",
            "engine": "gcp_enterprise_rest",
            "sources": [
                {
                    "name": "projects/123/locations/global/notebooks/nb-alpha/sources/src-1",
                    "sourceId": {"id": "src-1"},
                    "title": "ADR-001.md",
                    "metadata": {"wordCount": 420},
                    "status": "SOURCE_STATUS_COMPLETE",
                },
                {
                    "sourceId": "src-2",
                    "title": "API Reference",
                    "kind": "web",
                    "wordCount": 1250,
                    "status": "ready",
                },
                {
                    "sourceId": "src-3",
                    "title": "Roadmap.pdf",
                    "kind": "pdf",
                    "wordCount": 800,
                    "status": "ready",
                },
            ],
        }

    async def fake_notebook_sync_files(
        file_paths: list[str],
        notebook_id: str | None = None,
        wait: bool = False,
        engine: str = "auto",
        workspace_dir: str | None = None,
    ) -> dict[str, Any]:
        return {
            "engine": "rpc_bridge",
            "notebook_id": notebook_id or "nb-alpha",
            "uploaded": [{"file": f, "result": {"status": "ok"}} for f in file_paths],
        }

    async def fake_notebook_create(
        title: str,
        attach_to_workspace: bool = True,
        alias: str | None = None,
        emoji: str | None = None,
        engine: str = "auto",
        workspace_dir: str | None = None,
    ) -> dict[str, Any]:
        return {
            "notebookId": "nb-created",
            "title": title,
            "engine": "rpc_bridge",
            "attached_to_workspace": attach_to_workspace,
        }

    monkeypatch.setattr(server_mod, "notebook_list", fake_notebook_list)
    monkeypatch.setattr(server_mod, "notebook_get", fake_notebook_get)
    monkeypatch.setattr(server_mod, "notebook_sync_files", fake_notebook_sync_files)
    monkeypatch.setattr(server_mod, "notebook_create", fake_notebook_create)

    httpd = server_mod.create_server("127.0.0.1", 0, workspace_dir=str(tmp_path))
    port = httpd.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        # 1. Serve UI HTML
        with _DIRECT_OPENER.open(f"{base_url}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert resp.status == 200
            assert 'id="notebook-select"' in html
            assert 'id="sources-list"' in html
            assert 'id="sync-docs-btn"' in html

        # 2. GET /api/status (unattached)
        code, status_data = _http_json(f"{base_url}/api/status")
        assert code == 200
        assert status_data["status"] == "ok"
        assert status_data["attached_notebook"] is None
        assert "README.md" in status_data["workspace_docs"]
        assert "AGENTS.md" in status_data["workspace_docs"]

        # 3. GET /api/notebooks
        code, list_data = _http_json(f"{base_url}/api/notebooks")
        assert code == 200
        assert list_data["count"] == 2
        assert list_data["notebooks"][0]["notebookId"] == "nb-alpha"
        assert list_data["notebooks"][1]["notebookId"] == "nb-beta"

        # 4. POST /api/attach
        code, attach_data = _http_json(
            f"{base_url}/api/attach",
            method="POST",
            payload={
                "notebook_id": "projects/123/locations/global/notebooks/nb-alpha",
                "title": "Alpha Architecture",
                "active_source_ids": [],
            },
        )
        assert code == 200
        assert attach_data["status"] == "attached"
        assert attach_data["binding"]["notebook_id"] == "nb-alpha"
        assert (tmp_path / ".agents" / "notebook.json").is_file()

        # 5. GET /api/notebook?id=nb-alpha
        code, nb_data = _http_json(f"{base_url}/api/notebook?id=nb-alpha")
        assert code == 200
        assert nb_data["status"] == "ok"
        assert len(nb_data["sources"]) == 3
        assert nb_data["sources"][0]["sourceId"] == "src-1"
        assert nb_data["sources"][0]["wordCount"] == 420
        assert nb_data["sources"][0]["status"] == "complete"

        # 6. POST /api/sources/toggle (uncheck 1 source when all active -> remaining 2 active)
        code, toggle_data = _http_json(
            f"{base_url}/api/sources/toggle",
            method="POST",
            payload={
                "source_id": "src-1",
                "enabled": False,
                "all_source_ids": ["src-1", "src-2", "src-3"],
            },
        )
        assert code == 200
        assert toggle_data["active_source_ids"] == ["src-2", "src-3"]

        # Re-enable src-1 -> all 3 selected collapses back to [] ("all active")
        code, toggle_all = _http_json(
            f"{base_url}/api/sources/toggle",
            method="POST",
            payload={
                "source_id": "src-1",
                "enabled": True,
                "all_source_ids": ["src-1", "src-2", "src-3"],
            },
        )
        assert code == 200
        assert toggle_all["active_source_ids"] == []

        # Explicit active_source_ids list
        code, toggle_explicit = _http_json(
            f"{base_url}/api/sources/toggle",
            method="POST",
            payload={"active_source_ids": ["src-2"]},
        )
        assert code == 200
        assert toggle_explicit["active_source_ids"] == ["src-2"]

        # 7. POST /api/sync
        code, sync_data = _http_json(
            f"{base_url}/api/sync",
            method="POST",
            payload={"file_paths": ["README.md"]},
        )
        assert code == 200
        assert sync_data["status"] == "synced"
        assert len(sync_data["uploaded"]) == 1

        # 8. POST /api/create
        code, create_data = _http_json(
            f"{base_url}/api/create",
            method="POST",
            payload={"title": "Fresh Notebook"},
        )
        assert code == 200
        assert create_data["status"] == "created"
        assert create_data["notebook"]["notebookId"] == "nb-created"
    finally:
        httpd.shutdown()
        httpd.server_close()
