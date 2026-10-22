"""Interactive HTTP & JSON API server for the Antigravity 2 Gemini Notebook Sidecar Panel."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SIDECAR_DIR = Path(__file__).resolve().parent
REPO_ROOT = SIDECAR_DIR.parent.parent

# Ensure `agy_gemini_notebook` and local `.venv` packages are importable even when
# launched directly via system `python3 sidecars/notebook_context_panel/server.py`.
_VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"
if _VENV_PY.is_file():
    sys.executable = str(_VENV_PY)
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
for _venv_site in sorted((REPO_ROOT / ".venv" / "lib").glob("python*/site-packages")):
    if str(_venv_site) not in sys.path:
        sys.path.insert(0, str(_venv_site))

from agy_gemini_notebook.config import (
    WorkspaceNotebookBinding,
    describe_environment,
    load_workspace_binding,
     resolve_workspace_dir,
    save_workspace_binding,
)
from agy_gemini_notebook.engines.rpc_bridge import NotebookRpcBridge
from agy_gemini_notebook.mcp_server import (
    _extract_notebook_id,
    _select_engine,
    notebook_attach,
    notebook_create,
    notebook_get,
    notebook_list,
    notebook_login,
    notebook_sync_files,
)

DEFAULT_DOC_CANDIDATES: tuple[str, ...] = (
    "README.md",
    "AGENTS.md",
    "next-steps.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
)


def discover_workspace_docs(workspace_dir: str | Path | None = None) -> list[str]:
    """Return relative paths of common markdown/documentation files in the workspace."""
    root = resolve_workspace_dir(workspace_dir)
    found: list[str] = []
    for name in DEFAULT_DOC_CANDIDATES:
        candidate = root / name
        if candidate.is_file():
            found.append(name)

    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for doc_path in sorted(docs_dir.rglob("*.md"))[:15]:
            if doc_path.is_file():
                rel = str(doc_path.relative_to(root))
                if rel not in found:
                    found.append(rel)
    return found


def normalize_notebook_summary(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a notebook entry from either GCP REST v1alpha or RPC bridge."""
    raw_id = raw.get("notebookId") or raw.get("id") or ""
    if not raw_id and raw.get("name"):
        raw_id = _extract_notebook_id(str(raw["name"]))
    notebook_id = _extract_notebook_id(str(raw_id)) if raw_id else ""
    title = raw.get("title") or raw.get("displayName") or notebook_id or "Untitled Notebook"
    sources = raw.get("sources")
    sources_count = raw.get("sourcesCount")
    if sources_count is None and isinstance(sources, list):
        sources_count = len(sources)
    return {
        **raw,
        "notebookId": notebook_id,
        "title": title,
        "emoji": raw.get("emoji"),
        "sourcesCount": sources_count,
    }


def normalize_source_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a source object from either GCP REST v1alpha or RPC bridge for the UI."""
    raw_id: Any = raw.get("sourceId") or raw.get("id") or ""
    if isinstance(raw_id, dict):
        raw_id = raw_id.get("id") or ""
    if not raw_id and raw.get("name"):
        raw_id = str(raw["name"]).split("/")[-1]
    source_id = str(raw_id).strip()

    title = (
        raw.get("title")
        or raw.get("displayName")
        or raw.get("sourceName")
        or source_id
        or "Untitled Source"
    )
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    word_count = raw.get("wordCount")
    if word_count is None:
        word_count = metadata.get("wordCount")

    status = str(raw.get("status") or "ready").lower()
    if status.startswith("source_status_"):
        status = status.replace("source_status_", "")

    kind = str(raw.get("kind") or raw.get("sourceType") or "document").lower()
    return {
        **raw,
        "sourceId": source_id,
        "title": str(title),
        "kind": kind,
        "status": status,
        "wordCount": word_count,
    }


async def api_get_status(
    workspace_dir: str | None = None,
    engine: str = "auto",
) -> dict[str, Any]:
    """Handle `GET /api/status`."""
    info = describe_environment(workspace_dir)
    bridge = NotebookRpcBridge()
    selected_engine, _, _ = _select_engine(engine, workspace_dir)
    info["selected_engine"] = selected_engine
    info["rpc_bridge"] = {
        "notebooklm_py_installed": bridge.is_available(),
        "has_saved_session": bridge.has_saved_session(),
        "profile": bridge.profile or "default",
    }
    info["workspace_docs"] = discover_workspace_docs(workspace_dir)
    return {"status": "ok", **info}


async def api_list_notebooks(
    engine: str = "auto",
    query: str | None = None,
    page_size: int = 50,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Handle `GET /api/notebooks`."""
    try:
        res = await notebook_list(
            page_size=page_size,
            query=query,
            engine=engine,
            workspace_dir=workspace_dir,
        )
        if res.get("status") == "auth_required":
            return {
                "status": "auth_required",
                "engine": res.get("engine", engine),
                "count": 0,
                "notebooks": [],
                "error": res.get("error"),
                "remediation": res.get("remediation"),
            }
        notebooks = [normalize_notebook_summary(nb) for nb in res.get("notebooks", [])]
        return {
            "status": "ok",
            "engine": res.get("engine", engine),
            "count": len(notebooks),
            "notebooks": notebooks,
        }
    except Exception as exc:  # noqa: BLE001
        selected_engine, _, _ = _select_engine(engine, workspace_dir)
        return {
            "status": "error",
            "engine": selected_engine,
            "count": 0,
            "notebooks": [],
            "error": str(exc),
        }


async def api_get_notebook(
    notebook_id: str | None = None,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Handle `GET /api/notebook?id=<notebook_id>`."""
    binding = load_workspace_binding(workspace_dir)
    target_id = _extract_notebook_id(notebook_id) if notebook_id else (
        binding.notebook_id if binding else None
    )
    if not target_id:
        return {
            "status": "unattached",
            "notebook": None,
            "sources": [],
            "active_source_ids": [],
        }

    try:
        raw_nb = await notebook_get(
            notebook_id=target_id,
            engine=engine,
            workspace_dir=workspace_dir,
        )
        if raw_nb.get("status") == "auth_required":
            return {
                "status": "auth_required",
                "notebook_id": target_id,
                "notebook": None,
                "sources": [],
                "active_source_ids": [],
                "error": raw_nb.get("error"),
                "remediation": raw_nb.get("remediation"),
            }
        normalized_nb = normalize_notebook_summary(raw_nb)
        raw_sources = raw_nb.get("sources") or []
        sources = [normalize_source_item(s) for s in raw_sources if isinstance(s, dict)]
        normalized_nb["sources"] = sources

        active_ids: list[str] = []
        if binding and _extract_notebook_id(binding.notebook_id) == target_id:
            active_ids = list(binding.active_source_ids)

        return {
            "status": "ok",
            "engine": raw_nb.get("engine", engine),
            "notebook": normalized_nb,
            "sources": sources,
            "active_source_ids": active_ids,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "notebook_id": target_id,
            "notebook": None,
            "sources": [],
            "active_source_ids": (
                list(binding.active_source_ids)
                if binding and _extract_notebook_id(binding.notebook_id) == target_id
                else []
            ),
            "error": str(exc),
        }


async def api_attach_notebook(
    payload: dict[str, Any],
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Handle `POST /api/attach`."""
    ws = payload.get("workspace_dir") or workspace_dir
    notebook_id = str(payload.get("notebook_id") or "").strip()
    if not notebook_id:
        raise ValueError("`notebook_id` is required to attach a notebook.")

    title = payload.get("title")
    raw_active = payload.get("active_source_ids")
    active_source_ids = (
        [str(s).strip() for s in raw_active if str(s).strip()]
        if isinstance(raw_active, list)
        else []
    )
    alias = payload.get("alias")
    engine = str(payload.get("engine") or "auto")
    gcp_project_number = payload.get("gcp_project_number")

    return await notebook_attach(
        notebook_id=notebook_id,
        title=title,
        active_source_ids=active_source_ids,
        alias=alias,
        engine=engine,
        gcp_project_number=gcp_project_number,
        workspace_dir=ws,
    )


async def api_toggle_sources(
    payload: dict[str, Any],
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Handle `POST /api/sources/toggle`.

    Supports either:
    - Explicit list replacement: `{"active_source_ids": ["s1", "s2"]}`
    - Single source check/uncheck: `{"source_id": "s1", "enabled": false, "all_source_ids": ["s1", "s2"]}`
    """
    ws = payload.get("workspace_dir") or workspace_dir
    binding = load_workspace_binding(ws)
    notebook_id = str(payload.get("notebook_id") or (binding.notebook_id if binding else "")).strip()
    if not notebook_id:
        raise ValueError("No active notebook is attached to `.agents/notebook.json`.")

    clean_nb_id = _extract_notebook_id(notebook_id)
    if binding is None or _extract_notebook_id(binding.notebook_id) != clean_nb_id:
        binding = WorkspaceNotebookBinding(
            notebook_id=clean_nb_id,
            title=payload.get("title") or (binding.title if binding else None),
            engine=binding.engine if binding else "auto",
            aliases=dict(binding.aliases) if binding else {},
            gcp_project_number=binding.gcp_project_number if binding else None,
        )

    if "active_source_ids" in payload and isinstance(payload["active_source_ids"], list):
        updated_ids = [
            str(sid).strip() for sid in payload["active_source_ids"] if str(sid).strip()
        ]
    elif "source_id" in payload:
        target_sid = str(payload["source_id"]).strip()
        enabled = payload.get("enabled")
        all_ids = [
            str(sid).strip()
            for sid in (payload.get("all_source_ids") or [])
            if str(sid).strip()
        ]
        current = list(binding.active_source_ids)

        # In `.agents/notebook.json`, an empty `active_source_ids` list means "all sources active".
        # When unchecking a single source from the "all active" state, expand against `all_source_ids`.
        if not current and all_ids and enabled is False:
            current = list(all_ids)

        if enabled is True:
            if target_sid and target_sid not in current:
                current.append(target_sid)
        elif enabled is False:
            current = [sid for sid in current if sid != target_sid]
        else:
            if target_sid in current:
                current = [sid for sid in current if sid != target_sid]
            elif target_sid:
                current.append(target_sid)

        # Collapse back to `[]` ("all sources active") when every source in `all_source_ids` is selected
        if all_ids and set(current) >= set(all_ids):
            updated_ids = []
        else:
            updated_ids = current
    else:
        raise ValueError("Provide either `active_source_ids` or `source_id` in request body.")

    binding.active_source_ids = updated_ids
    saved_path = save_workspace_binding(binding, ws)
    return {
        "status": "updated",
        "binding_file": str(saved_path),
        "active_source_ids": binding.active_source_ids,
        "binding": binding.model_dump(exclude_none=True),
    }


async def api_sync_workspace_docs(
    payload: dict[str, Any],
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Handle `POST /api/sync` to upload workspace documentation files to the active notebook."""
    ws = payload.get("workspace_dir") or workspace_dir
    root = resolve_workspace_dir(ws)
    raw_files = payload.get("file_paths")
    if isinstance(raw_files, list) and raw_files:
        rel_or_abs = [str(f).strip() for f in raw_files if str(f).strip()]
    else:
        rel_or_abs = discover_workspace_docs(root)

    if not rel_or_abs:
        raise ValueError("No workspace documentation files found to synchronize.")

    resolved_paths: list[str] = []
    for fpath in rel_or_abs:
        candidate = Path(fpath)
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        if candidate.is_file():
            resolved_paths.append(str(candidate))

    if not resolved_paths:
        raise FileNotFoundError(f"None of the requested files exist in {root}: {rel_or_abs}")

    res = await notebook_sync_files(
        file_paths=resolved_paths,
        notebook_id=payload.get("notebook_id"),
        wait=bool(payload.get("wait", False)),
        engine=str(payload.get("engine") or "auto"),
        workspace_dir=str(root),
    )
    return {
        "status": "synced",
        **res,
    }


async def api_create_notebook(
    payload: dict[str, Any],
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Handle `POST /api/create` to create and attach a new notebook from the Sidecar UI."""
    ws = payload.get("workspace_dir") or workspace_dir
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("`title` is required to create a notebook.")

    created = await notebook_create(
        title=title,
        attach_to_workspace=bool(payload.get("attach_to_workspace", True)),
        alias=payload.get("alias"),
        emoji=payload.get("emoji"),
        engine=str(payload.get("engine") or "auto"),
        workspace_dir=ws,
    )
    if created.get("status") == "auth_required":
        return created
    return {
        "status": "created",
        "notebook": normalize_notebook_summary(created),
    }


async def api_login_notebook(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle `POST /api/login` to launch interactive NotebookLM browser authentication."""
    return await notebook_login(
        browser=str(payload.get("browser") or "chromium"),
        timeout=int(payload.get("timeout") or 300),
        browser_cookies=payload.get("browser_cookies"),
        account=payload.get("account"),
        fresh=bool(payload.get("fresh", False)),
    )


def _inject_sidecar_query_params(html: str, token: str | None, conversation_id: str | None) -> str:
    """Append `token` and `conversationId` query parameters to local `/...` links in HTML."""
    if not token:
        return html
    import re
    from urllib.parse import quote

    extra = f"token={quote(token)}"
    if conversation_id:
        extra += f"&conversationId={quote(conversation_id)}"

    def _repl(match: re.Match[str]) -> str:
        attr, quote_char, url = match.group(1), match.group(2), match.group(3)
        if url.startswith("//"):
            return match.group(0)
        parts = url.split("#", 1)
        base = parts[0]
        frag = f"#{parts[1]}" if len(parts) > 1 else ""
        sep = "&" if "?" in base else "?"
        return f"{attr}={quote_char}{base}{sep}{extra}{frag}{quote_char}"

    return re.sub(r"\b(href|src)=([\"'])(/[^\"'\s>]*)\2", _repl, html)


def _load_preload_js() -> str:
    """Return the Antigravity sidecar `preload.js` bridge script or a compatible stub."""
    app_data = os.environ.get("ANTIGRAVITY_APP_DATA_DIR", "").strip()
    candidates = []
    if app_data:
        candidates.append(Path(app_data) / "lib" / "node_modules" / "sidecar_sdk" / "preload.js")
    candidates.append(
        Path.home() / ".gemini" / "antigravity" / "lib" / "node_modules" / "sidecar_sdk" / "preload.js"
    )
    for candidate in candidates:
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                pass
    return "// Preload bridge\nwindow.__sidecarReady = true;\n"


class SidecarRequestHandler(SimpleHTTPRequestHandler):
    """Serves `ui.html`, static assets, and `/api/*` JSON endpoints for the Aux Pane sidecar."""

    workspace_dir: str | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(SIDECAR_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("SIDECAR_QUIET") == "1":
            return
        super().log_message(format, *args)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sidecar-Token")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _send_bytes(
        self,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
        status: int = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, content_type="application/json; charset=utf-8", status=status)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("Expected a JSON object in request body.")
        return data

    def _effective_workspace_dir(self, query_params: dict[str, list[str]]) -> str | None:
        if query_params.get("workspace_dir"):
            return query_params["workspace_dir"][0]
        return self.workspace_dir

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        params = parse_qs(parsed.query)
        ws = self._effective_workspace_dir(params)
        engine = (params.get("engine") or ["auto"])[0]

        if route == "/preload.js":
            self._send_bytes(
                _load_preload_js().encode("utf-8"),
                content_type="application/javascript; charset=utf-8",
            )
            return

        if route == "/shell.css":
            self._send_bytes(
                b"/* Sidecar shell styles */\n",
                content_type="text/css; charset=utf-8",
            )
            return

        if route in ("/", "", "/ui.html"):
            ui_path = SIDECAR_DIR / "ui.html"
            html = ui_path.read_text(encoding="utf-8")
            token = (
                (params.get("token") or [None])[0]
                or (self.headers.get("X-Sidecar-Token") or "").strip()
                or None
            )
            conv_id = (
                (params.get("conversationId") or params.get("conversation_id") or [None])[0]
                or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
            )
            html = _inject_sidecar_query_params(html, token, conv_id)
            self._send_bytes(html.encode("utf-8"), content_type="text/html; charset=utf-8")
            return

        if route == "/api/status":
            data = asyncio.run(api_get_status(workspace_dir=ws, engine=engine))
            self._send_json(data)
            return

        if route == "/api/notebooks":
            query = (params.get("query") or [None])[0]
            page_size = int((params.get("page_size") or ["50"])[0])
            data = asyncio.run(
                api_list_notebooks(
                    engine=engine,
                    query=query,
                    page_size=page_size,
                    workspace_dir=ws,
                )
            )
            self._send_json(data)
            return

        if route == "/api/notebook":
            notebook_id = (params.get("id") or params.get("notebook_id") or [None])[0]
            data = asyncio.run(
                api_get_notebook(
                    notebook_id=notebook_id,
                    engine=engine,
                    workspace_dir=ws,
                )
            )
            self._send_json(data)
            return

        if route.startswith("/api/"):
            self._send_json(
                {"status": "error", "error": f"Unknown API endpoint: {route}"},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        params = parse_qs(parsed.query)
        ws = self._effective_workspace_dir(params)

        try:
            payload = self._read_json_body()
        except Exception as exc:  # noqa: BLE001
            self._send_json(
                {"status": "error", "error": f"Invalid JSON body: {exc}"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            if route == "/api/attach":
                data = asyncio.run(api_attach_notebook(payload, workspace_dir=ws))
                self._send_json(data)
                return

            if route == "/api/sources/toggle":
                data = asyncio.run(api_toggle_sources(payload, workspace_dir=ws))
                self._send_json(data)
                return

            if route == "/api/sync":
                data = asyncio.run(api_sync_workspace_docs(payload, workspace_dir=ws))
                self._send_json(data)
                return

            if route == "/api/create":
                data = asyncio.run(api_create_notebook(payload, workspace_dir=ws))
                self._send_json(data)
                return

            if route == "/api/login":
                data = asyncio.run(api_login_notebook(payload))
                self._send_json(data)
                return

            self._send_json(
                {"status": "error", "error": f"Unknown API endpoint: {route}"},
                status=HTTPStatus.NOT_FOUND,
            )
        except ValueError as exc:
            self._send_json(
                {"status": "error", "error": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:  # noqa: BLE001
            self._send_json(
                {"status": "error", "error": str(exc)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    workspace_dir: str | None = None,
) -> ThreadingHTTPServer:
    """Create a configured `ThreadingHTTPServer` instance (useful for testing and embedding)."""

    class BoundSidecarHandler(SidecarRequestHandler):
        pass

    BoundSidecarHandler.workspace_dir = workspace_dir
    return ThreadingHTTPServer((host, port), BoundSidecarHandler)


def resolve_sidecar_port(default: int = 8000) -> int:
    """Resolve the HTTP port assigned by Antigravity 2 (`ANTIGRAVITY_SIDECAR_WEB_PORT`) or `PORT`."""
    for env_key in ("ANTIGRAVITY_SIDECAR_WEB_PORT", "PORT"):
        raw = os.environ.get(env_key, "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError:
                continue
    return default


def _sidecar_data_dirs() -> list[Path]:
    """Return candidate Antigravity 2 sidecar data directories for `server_info.json`."""
    dirs: list[Path] = []
    exec_dir = os.environ.get("ANTIGRAVITY_EXECUTABLE_DATA_DIR", "").strip()
    if exec_dir:
        dirs.append(Path(exec_dir))
    app_data = os.environ.get("ANTIGRAVITY_APP_DATA_DIR", "").strip()
    base = Path(app_data) if app_data else (Path.home() / ".gemini" / "antigravity")
    dirs.append(base / "sidecar_data" / "gemini-notebook" / "notebook_context_panel" / "data")
    dirs.append(base / "sidecar_data" / "notebook_context_panel" / "data")
    return dirs


def _write_server_info(host: str, port: int) -> list[Path]:
    """Write `server_info.json` so Antigravity 2 can discover the active sidecar port and token."""
    token = (
        os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN")
        or os.environ.get("ANTIGRAVITY_SIDECAR_AUTH_TOKEN")
        or os.environ.get("ANTIGRAVITY_SIDECAR_WEB_TOKEN")
    )
    payload = json.dumps(
        {
            "host": host,
            "port": port,
            "token": token,
            "pid": os.getpid(),
        },
        indent=2,
    )
    written: list[Path] = []
    for d in _sidecar_data_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            info_path = d / "server_info.json"
            info_path.write_text(payload + "\n", encoding="utf-8")
            os.chmod(info_path, 0o600)
            written.append(info_path)
        except OSError:
            continue
    return written


def main() -> None:
    import signal

    port = resolve_sidecar_port(default=8000)
    server = create_server("127.0.0.1", port)
    actual_port = int(server.server_address[1])
    info_files = _write_server_info("127.0.0.1", actual_port)

    def _cleanup(*_args: Any) -> None:
        for p in info_files:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    print(
        f"Gemini Notebook by Chryzalide Sidecar UI listening on http://127.0.0.1:{actual_port}/ui.html",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        for p in info_files:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    main()
