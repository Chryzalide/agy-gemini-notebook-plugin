"""RPC Bridge Engine for Conversational Q&A, Deep Research, Studio Artifacts & Consumer Accounts.

Why this engine exists alongside `gcp_enterprise_rest.py`:
- The official Google Cloud Discovery Engine `v1alpha/projects.locations.notebooks` REST API
  handles Notebook CRUD, sharing, source ingestion (`batchCreate`, `uploadFile`), and
  `audioOverviews`, but does NOT yet expose a `:query` or `:converse` endpoint on notebooks,
  nor does it expose Web/Drive Deep Research (`research.start`), passage search (`sources.search`),
  or full source text extraction (`sources.get_fulltext`).
- Furthermore, personal Google accounts (`notebooklm.google.com`) do not have a GCP project.
- This bridge wraps `notebooklm-py` so that every notebook operation—CRUD, file upload,
  source ingestion, grounded Q&A, passage search, Fast/Deep Research, Studio artifacts,
  and persistent notes—works seamlessly across both consumer and enterprise environments.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from agy_gemini_notebook.config import PluginConfig

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FALLBACK_AUTH_DIR = PLUGIN_ROOT / ".notebooklm-auth"


class NotebookAuthRequiredError(RuntimeError):
    """Raised when no authenticated NotebookLM or GCP Enterprise session is available."""



def _serialize_source(src: Any) -> dict[str, Any]:
    """Normalize a `notebooklm.types.Source` object (or dict) into a JSON-serializable dict."""
    if isinstance(src, dict):
        return src
    status_val = getattr(src, "status", None)
    status_str = getattr(status_val, "name", str(status_val)).lower() if status_val else "ready"
    kind_val = getattr(src, "kind", None)
    kind_str = getattr(kind_val, "value", str(kind_val)) if kind_val else "unknown"
    created_at = getattr(src, "created_at", None)
    return {
        "sourceId": getattr(src, "id", str(src)),
        "title": getattr(src, "title", "") or "",
        "kind": kind_str,
        "status": status_str,
        "url": getattr(src, "url", None),
        "driveDocumentId": getattr(src, "drive_document_id", None),
        "wordCount": getattr(src, "word_count", None),
        "createdAt": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }


def _serialize_reference(ref: Any) -> dict[str, Any]:
    """Normalize a `ChatReference` object into a JSON-serializable citation dict."""
    if isinstance(ref, dict):
        return ref
    return {
        "source_id": getattr(ref, "source_id", ""),
        "citation_number": getattr(ref, "citation_number", None),
        "cited_text": getattr(ref, "cited_text", None),
        "chunk_id": getattr(ref, "chunk_id", None),
        "score": getattr(ref, "score", None),
        "start_char": getattr(ref, "start_char", None),
        "end_char": getattr(ref, "end_char", None),
    }


def _serialize_research_source(src: Any) -> dict[str, Any]:
    """Normalize a `ResearchSource` object into a JSON-serializable dict."""
    if isinstance(src, dict):
        return src
    if hasattr(src, "to_public_dict") and callable(src.to_public_dict):
        with suppress(Exception):
            return src.to_public_dict()
    result_type = getattr(src, "result_type", None)
    return {
        "title": getattr(src, "title", ""),
        "url": getattr(src, "url", ""),
        "result_type": getattr(result_type, "name", str(result_type)) if result_type else "web",
        "research_task_id": getattr(src, "research_task_id", None),
        "report_markdown": getattr(src, "report_markdown", ""),
        "hint": getattr(src, "hint", ""),
    }


def _serialize_research_task(task: Any) -> dict[str, Any]:
    """Normalize a `ResearchTask` object into a JSON-serializable dict."""
    if isinstance(task, dict):
        return task
    status_val = getattr(task, "status", None)
    status_str = getattr(status_val, "name", str(status_val)).lower() if status_val else "completed"
    raw_sources = getattr(task, "sources", ()) or ()
    return {
        "task_id": getattr(task, "task_id", ""),
        "status": status_str,
        "query": getattr(task, "query", ""),
        "summary": getattr(task, "summary", "") or "",
        "report": getattr(task, "report", "") or "",
        "sources": [_serialize_research_source(s) for s in raw_sources],
        "source_count": len(raw_sources),
    }


class NotebookRpcBridge:
    """Adapter around `notebooklm-py` for full Notebook CRUD, Q&A, Research, and Studio Artifacts."""

    def __init__(
        self,
        config: PluginConfig | None = None,
        profile: str | None = None,
        storage_path: str | None = None,
    ) -> None:
        self.config = config or PluginConfig()
        self.profile = profile or self.config.notebooklm_profile
        self.storage_path = storage_path or self.config.notebooklm_storage_path

    @staticmethod
    def is_available() -> bool:
        """Check whether the optional `notebooklm-py` package is installed."""
        try:
            import notebooklm  # noqa: F401

            return True
        except ImportError:
            return False

    def resolve_storage_path(self) -> Path | None:
        """Return the first existing `storage_state.json` path across user home and plugin fallback."""
        if self.storage_path:
            explicit = Path(self.storage_path).expanduser()
            if explicit.is_file():
                return explicit

        prof = self.profile or "default"
        candidates: list[Path] = []
        if self.is_available():
            with suppress(Exception):
                from notebooklm.paths import get_storage_path

                p = get_storage_path(profile=self.profile) if self.profile else get_storage_path()
                candidates.append(Path(p))

        default_home = Path(os.environ.get("NOTEBOOKLM_HOME", "~/.notebooklm")).expanduser()
        candidates.extend(
            [
                default_home / "profiles" / prof / "storage_state.json",
                default_home / "storage_state.json",
                FALLBACK_AUTH_DIR / "profiles" / prof / "storage_state.json",
                FALLBACK_AUTH_DIR / "storage_state.json",
            ]
        )
        for cand in candidates:
            with suppress(OSError):
                if cand.is_file():
                    return cand
        return None

    def has_saved_session(self) -> bool:
        """Check whether a saved `notebooklm` authentication state exists on disk or in env."""
        if os.environ.get("NOTEBOOKLM_AUTH_JSON"):
            return True
        if not self.is_available() and not self.storage_path:
            return False
        return self.resolve_storage_path() is not None

    @staticmethod
    def _resolve_python_executable() -> str:
        """Return the plugin `.venv/bin/python` executable if available, else `sys.executable`."""
        candidates = [
            PLUGIN_ROOT / ".venv" / "bin" / "python",
            PLUGIN_ROOT / ".venv" / "bin" / "python3",
            Path.home()
            / ".gemini"
            / "config"
            / "plugins"
            / "gemini-notebook"
            / ".venv"
            / "bin"
            / "python",
        ]
        for cand in candidates:
            with suppress(OSError):
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand)
        return sys.executable

    @staticmethod
    def _prepare_playwright_browsers_env(env: dict[str, str] | None = None) -> str | None:
        """Bridge existing `~/.cache/ms-playwright/chromium-*` builds if Playwright's expected revision is missing."""
        target_env = env if env is not None else os.environ
        if target_env.get("PLAYWRIGHT_BROWSERS_PATH"):
            return target_env.get("PLAYWRIGHT_BROWSERS_PATH")
        with suppress(Exception):
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                expected_exec = Path(pw.chromium.executable_path)
            if expected_exec.is_file():
                return str(expected_exec.parent.parent.parent)

            ms_cache = Path.home() / ".cache" / "ms-playwright"
            if not ms_cache.is_dir():
                return None
            existing_revs = sorted(
                [
                    d
                    for d in ms_cache.glob("chromium-*")
                    if d.is_dir() and (d / "chrome-linux64" / "chrome").is_file()
                ],
                reverse=True,
            )
            if not existing_revs:
                return None

            expected_rev_name = expected_exec.parent.parent.name
            bridge_dir = FALLBACK_AUTH_DIR / "playwright-browsers"
            bridge_dir.mkdir(parents=True, exist_ok=True)
            target_link = bridge_dir / expected_rev_name
            if not target_link.exists():
                target_link.symlink_to(existing_revs[0])
            target_env["PLAYWRIGHT_BROWSERS_PATH"] = str(bridge_dir)
            return str(bridge_dir)
        return None

    async def login(
        self,
        browser: str = "chromium",
        timeout: int = 300,
        browser_cookies: str | None = None,
        account: str | None = None,
        fresh: bool = False,
    ) -> dict[str, Any]:
        """Authenticate with Google NotebookLM via interactive browser login or browser cookie import."""
        if not self.is_available():
            raise RuntimeError(
                "Optional dependency `notebooklm-py` is not installed. "
                "Install it with `pip install -e '.[rpc]'`."
            )

        prof = self.profile or "default"
        fallback_storage = FALLBACK_AUTH_DIR / "profiles" / prof / "storage_state.json"
        fallback_storage.parent.mkdir(parents=True, exist_ok=True)

        python_exe = self._resolve_python_executable()
        cmd = [
            python_exe,
            "-m",
            "notebooklm",
            "--storage",
            str(fallback_storage),
            "login",
            "--browser-timeout",
            str(timeout),
        ]
        if browser_cookies:
            cmd.extend(["--browser-cookies", browser_cookies])
            if account:
                cmd.extend(["--account", account])
        else:
            cmd.extend(["--browser", browser])
        if fresh:
            cmd.append("--fresh")

        env = dict(os.environ)
        env["NOTEBOOKLM_HOME"] = str(FALLBACK_AUTH_DIR)
        venv_site_dirs = [
            str(p) for p in sorted((PLUGIN_ROOT / ".venv" / "lib").glob("python*/site-packages"))
        ]
        if venv_site_dirs:
            existing_py_path = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = ":".join(
                [*venv_site_dirs, str(PLUGIN_ROOT / "src"), *([existing_py_path] if existing_py_path else [])]
            )
        venv_bin = PLUGIN_ROOT / ".venv" / "bin"
        if venv_bin.is_dir():
            env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"

        if not browser_cookies and browser == "chromium":
            await asyncio.to_thread(self._prepare_playwright_browsers_env, env)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode == 0 and fallback_storage.is_file():
            with suppress(OSError):
                user_home_storage = (
                    Path.home() / ".notebooklm" / "profiles" / prof / "storage_state.json"
                )
                user_home_storage.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(fallback_storage, user_home_storage)

            return {
                "status": "authenticated",
                "authenticated": True,
                "engine": "rpc_bridge",
                "profile": prof,
                "storage_path": str(self.resolve_storage_path() or fallback_storage),
                "output": stdout_text,
            }

        return {
            "status": "login_failed",
            "authenticated": self.has_saved_session(),
            "engine": "rpc_bridge",
            "return_code": proc.returncode,
            "error": stderr_text or stdout_text or "NotebookLM login did not complete.",
        }

    @asynccontextmanager
    async def _open_client(self) -> AsyncIterator[Any]:
        """Open an authenticated `NotebookLMClient` context manager."""
        if not self.is_available():
            raise RuntimeError(
                "Optional dependency `notebooklm-py` is not installed. "
                "Install it with `pip install -e '.[rpc]'` (or `uv sync --extra rpc`) "
                "and run `agy-gemini-notebook login`."
            )
        from notebooklm import NotebookLMClient

        kwargs: dict[str, Any] = {}
        resolved_storage = self.resolve_storage_path()
        if self.storage_path:
            kwargs["path"] = self.storage_path
        elif resolved_storage is not None:
            kwargs["path"] = str(resolved_storage)
        if self.profile and "path" not in kwargs:
            kwargs["profile"] = self.profile

        try:
            ctx = NotebookLMClient.from_storage(**kwargs)
            if hasattr(ctx, "__aenter__"):
                async with ctx as client:
                    yield client
            else:
                # Support coroutine-returning mocks or legacy v0.4 `from_storage()`
                resolved = await ctx
                async with resolved as client:
                    yield client
        except FileNotFoundError as exc:
            raise NotebookAuthRequiredError(
                "No authenticated Gemini Notebook (NotebookLM) session found. "
                "Call the `notebook_login` MCP tool, run `agy-gemini-notebook login` in your terminal, "
                "or set `GCP_PROJECT_NUMBER` with `gcloud auth login` for Google Cloud Enterprise."
            ) from exc

    # -------------------------------------------------------------------------
    # Notebooks CRUD & Metadata
    # -------------------------------------------------------------------------

    async def list_notebooks(self) -> list[dict[str, Any]]:
        """List notebooks via the RPC bridge (`notebooklm-py`)."""
        async with self._open_client() as client:
            notebooks = await client.notebooks.list()
            result: list[dict[str, Any]] = []
            for nb in notebooks:
                created_at = getattr(nb, "created_at", None)
                modified_at = getattr(nb, "modified_at", None)
                result.append(
                    {
                        "notebookId": getattr(nb, "id", str(nb)),
                        "title": getattr(nb, "title", ""),
                        "emoji": getattr(nb, "emoji", None),
                        "sourcesCount": getattr(nb, "sources_count", 0),
                        "isOwner": getattr(nb, "is_owner", True),
                        "createdAt": (
                            created_at.isoformat()
                            if hasattr(created_at, "isoformat")
                            else created_at
                        ),
                        "modifiedAt": (
                            modified_at.isoformat()
                            if hasattr(modified_at, "isoformat")
                            else modified_at
                        ),
                    }
                )
            return result

    async def get_notebook(
        self,
        notebook_id: str,
        include_summary: bool = True,
    ) -> dict[str, Any]:
        """Get notebook metadata, AI description/suggested topics, and normalized source list."""
        async with self._open_client() as client:
            nb = await client.notebooks.get(notebook_id)
            sources = await client.sources.list(notebook_id)
            normalized_sources = [_serialize_source(s) for s in sources]

            summary_text = ""
            suggested_topics: list[str] = []
            if include_summary and hasattr(client.notebooks, "get_description"):
                with suppress(Exception):
                    desc = await client.notebooks.get_description(notebook_id)
                    summary_text = getattr(desc, "summary", "") or ""
                    raw_topics = getattr(desc, "suggested_topics", []) or []
                    suggested_topics = [
                        getattr(t, "question", str(t)) for t in raw_topics
                    ]

            nb_id = getattr(nb, "id", notebook_id)
            return {
                "notebookId": nb_id,
                "name": f"notebooks/{nb_id}",
                "title": getattr(nb, "title", ""),
                "emoji": getattr(nb, "emoji", None),
                "sourcesCount": getattr(nb, "sources_count", len(normalized_sources)),
                "isOwner": getattr(nb, "is_owner", True),
                "summary": summary_text,
                "suggestedTopics": suggested_topics,
                "sources": normalized_sources,
                "engine": "rpc_bridge",
            }

    async def create_notebook(
        self,
        title: str,
        emoji: str | None = None,
    ) -> dict[str, Any]:
        """Create a new Gemini Notebook via the RPC bridge (`notebooks.create`)."""
        async with self._open_client() as client:
            nb = await client.notebooks.create(title)
            nb_id = getattr(nb, "id", str(nb))
            if emoji and hasattr(client.notebooks, "set_emoji"):
                with suppress(Exception):
                    nb = await client.notebooks.set_emoji(nb_id, emoji)
            return {
                "notebookId": nb_id,
                "name": f"notebooks/{nb_id}",
                "title": getattr(nb, "title", title),
                "emoji": getattr(nb, "emoji", emoji),
                "engine": "rpc_bridge",
            }

    async def delete_notebooks(self, notebook_ids: list[str]) -> dict[str, Any]:
        """Delete one or more notebooks via the RPC bridge (`notebooks.delete`)."""
        async with self._open_client() as client:
            for nid in notebook_ids:
                await client.notebooks.delete(nid)
            return {
                "deleted": notebook_ids,
                "engine": "rpc_bridge",
            }

    async def rename_notebook(self, notebook_id: str, new_title: str) -> dict[str, Any]:
        """Rename a notebook via the RPC bridge (`notebooks.rename`)."""
        async with self._open_client() as client:
            nb = await client.notebooks.rename(notebook_id, new_title)
            return {
                "notebookId": getattr(nb, "id", notebook_id),
                "title": getattr(nb, "title", new_title),
                "engine": "rpc_bridge",
            }

    async def share_notebook(
        self,
        notebook_id: str,
        public: bool = True,
    ) -> dict[str, Any]:
        """Enable or disable public link sharing for a notebook via the RPC bridge."""
        async with self._open_client() as client:
            if hasattr(client, "sharing") and hasattr(client.sharing, "set_public"):
                await client.sharing.set_public(notebook_id, public=public)
            share_url = ""
            if hasattr(client.notebooks, "get_share_url"):
                share_url = client.notebooks.get_share_url(notebook_id)
            return {
                "notebookId": notebook_id,
                "public": public,
                "shareUrl": share_url,
                "engine": "rpc_bridge",
            }

    # -------------------------------------------------------------------------
    # Sources Ingestion, Upload, Search & Fulltext
    # -------------------------------------------------------------------------

    async def upload_file_source(
        self,
        notebook_id: str,
        file_path: str | Path,
        display_name: str | None = None,
        wait: bool = False,
        wait_timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Upload a local file to a notebook via `client.sources.add_file`."""
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        async with self._open_client() as client:
            kwargs: dict[str, Any] = {"wait": wait, "wait_timeout": wait_timeout}
            if display_name:
                kwargs["title"] = display_name
            src = await client.sources.add_file(notebook_id, path, **kwargs)
            return {
                "notebookId": notebook_id,
                "source": _serialize_source(src),
                "engine": "rpc_bridge",
            }

    async def add_source(
        self,
        notebook_id: str,
        source_type: str,
        value: str,
        title: str = "Workspace Source",
        mime_type: str = "application/vnd.google-apps.document",
        wait: bool = False,
        wait_timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Add a text, web URL, YouTube video, or Google Drive source via `client.sources`."""
        if source_type not in ("text", "web", "youtube", "gdrive"):
            raise ValueError(
                f"Unsupported source_type '{source_type}'. Expected 'text', 'web', 'youtube', or 'gdrive'."
            )

        async with self._open_client() as client:
            if source_type == "text":
                src = await client.sources.add_text(
                    notebook_id,
                    title,
                    value,
                    wait=wait,
                    wait_timeout=wait_timeout,
                )
            elif source_type in ("web", "youtube"):
                url_kwargs: dict[str, Any] = {"wait": wait, "wait_timeout": wait_timeout}
                if title and title != "Workspace Source":
                    url_kwargs["title"] = title
                src = await client.sources.add_url(notebook_id, value, **url_kwargs)
            else:
                src = await client.sources.add_drive(
                    notebook_id,
                    value,
                    title,
                    mime_type=mime_type,
                    wait=wait,
                    wait_timeout=wait_timeout,
                )
            return {
                "notebookId": notebook_id,
                "source": _serialize_source(src),
                "engine": "rpc_bridge",
            }

    async def delete_sources(
        self,
        notebook_id: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        """Delete one or more sources from a notebook via `client.sources.delete`."""
        async with self._open_client() as client:
            for sid in source_ids:
                await client.sources.delete(notebook_id, sid)
            return {
                "notebookId": notebook_id,
                "deletedSourceIds": source_ids,
                "engine": "rpc_bridge",
            }

    async def search_sources(
        self,
        notebook_id: str,
        query: str,
        source_ids: list[str] | None = None,
        limit: int | None = 10,
    ) -> dict[str, Any]:
        """Search indexed passages across notebook sources via `client.sources.search`."""
        async with self._open_client() as client:
            chunks = await client.sources.search(
                notebook_id,
                query,
                source_ids=source_ids,
                limit=limit,
            )
            matches = [
                {
                    "source_id": getattr(c, "source_id", ""),
                    "text": getattr(c, "text", str(c)),
                    "rank": getattr(c, "rank", idx + 1),
                    "start": getattr(c, "start", None),
                    "end": getattr(c, "end", None),
                }
                for idx, c in enumerate(chunks)
            ]
            return {
                "notebook_id": notebook_id,
                "query": query,
                "count": len(matches),
                "matches": matches,
                "engine": "rpc_bridge",
            }

    async def get_source_fulltext(
        self,
        notebook_id: str,
        source_id: str,
        include_guide: bool = False,
    ) -> dict[str, Any]:
        """Retrieve the full indexed text (and optional AI guide) of a source via RPC."""
        if not self.is_available():
            raise RuntimeError(
                "Full-text source extraction requires `notebooklm-py` (`pip install -e '.[rpc]'`) "
                "because the official v1alpha `sources.get` endpoint only returns metadata "
                "(wordCount, tokenCount, status)."
            )

        async with self._open_client() as client:
            source = await client.sources.get_fulltext(notebook_id, source_id)
            content = getattr(source, "content", str(source))
            payload: dict[str, Any] = {
                "notebook_id": notebook_id,
                "source_id": source_id,
                "title": getattr(source, "title", ""),
                "url": getattr(source, "url", None),
                "char_count": getattr(source, "char_count", len(content)),
                "content": content,
                "engine": "rpc_bridge",
            }
            if include_guide and hasattr(client.sources, "get_guide"):
                with suppress(Exception):
                    guide = await client.sources.get_guide(notebook_id, source_id)
                    payload["guide"] = {
                        "summary": getattr(guide, "summary", ""),
                        "keywords": list(getattr(guide, "keywords", []) or []),
                    }
            return payload

    # -------------------------------------------------------------------------
    # Grounded Conversational Q&A (`client.chat`)
    # -------------------------------------------------------------------------

    async def query_notebook(
        self,
        notebook_id: str,
        question: str,
        source_ids: list[str] | None = None,
        conversation_id: str | None = None,
        save_as_note: bool = False,
        note_title: str | None = None,
    ) -> dict[str, Any]:
        """Ask a grounded question against a Gemini Notebook and return answer + citations."""
        if not self.is_available():
            return {
                "status": "rpc_unavailable",
                "notebook_id": notebook_id,
                "question": question,
                "message": (
                    "The official Google Cloud Discovery Engine v1alpha Notebooks REST API "
                    "supports notebook & source management, while conversational Q&A requires "
                    "the RPC bridge (`pip install -e '.[rpc]'` + `notebooklm login`)."
                ),
            }

        async with self._open_client() as client:
            kwargs: dict[str, Any] = {}
            if source_ids:
                kwargs["source_ids"] = source_ids
            if conversation_id:
                kwargs["conversation_id"] = conversation_id
            result = await client.chat.ask(notebook_id, question, **kwargs)

            raw_refs = getattr(result, "references", None)
            if raw_refs is None:
                raw_refs = getattr(result, "citations", [])
            citations = [_serialize_reference(r) for r in (raw_refs or [])]

            raw_next = getattr(result, "next_steps", []) or []
            next_steps = [getattr(ns, "question", str(ns)) for ns in raw_next]

            response: dict[str, Any] = {
                "status": "ok",
                "engine": "rpc_bridge",
                "notebook_id": notebook_id,
                "question": question,
                "answer": getattr(result, "answer", str(result)),
                "conversation_id": getattr(result, "conversation_id", conversation_id),
                "turn_number": getattr(result, "turn_number", None),
                "citations": citations,
                "next_steps": next_steps,
            }

            if save_as_note and hasattr(client.chat, "save_answer_as_note"):
                try:
                    note = await client.chat.save_answer_as_note(
                        notebook_id,
                        result,
                        title=note_title,
                    )
                    response["saved_note"] = {
                        "id": getattr(note, "id", ""),
                        "title": getattr(note, "title", note_title or ""),
                    }
                except Exception as exc:  # noqa: BLE001
                    response["saved_note_error"] = str(exc)

            return response

    # -------------------------------------------------------------------------
    # Autonomous Web / Drive Research & Deep Research (`client.research`)
    # -------------------------------------------------------------------------

    async def _import_task_sources(
        self,
        client: Any,
        notebook_id: str,
        task: Any,
        cited_only: bool = False,
        allow_duplicate: bool = False,
    ) -> list[dict[str, Any]]:
        """Import discovered sources from a completed ResearchTask into the notebook."""
        raw_sources = list(getattr(task, "sources", ()) or ())
        if not raw_sources:
            return []

        selected = raw_sources
        report_text = getattr(task, "report", "") or ""
        if cited_only and report_text and hasattr(client.research, "select_cited_sources"):
            selection = client.research.select_cited_sources(raw_sources, report_text)
            selected = list(getattr(selection, "sources", raw_sources))

        task_id = getattr(task, "task_id", "")
        if hasattr(client.research, "import_sources_with_verification"):
            return await client.research.import_sources_with_verification(
                notebook_id,
                task_id,
                selected,
                allow_duplicate=allow_duplicate,
            )
        return await client.research.import_sources(notebook_id, task_id, selected)

    async def run_research(
        self,
        notebook_id: str,
        query: str,
        mode: str = "fast",
        source: str = "web",
        wait: bool = True,
        import_sources: bool = True,
        cited_only: bool = False,
        timeout: float = 600.0,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Launch a Fast Research, Deep Research, or synchronous Source Discovery session.

        Args:
            notebook_id: Target Gemini Notebook ID.
            query: Research topic or question (can be empty only when mode is 'curious'/'curious_raw').
            mode: One of:
                - 'fast': Rapid multi-source search across web or Google Drive.
                - 'deep': Autonomous Deep Research (multi-step exploration + synthesized markdown report).
                - 'discover' (or 'raw', 'curious', 'curious_raw'): One-shot synchronous source discovery.
            source: 'web' or 'drive' (used for 'fast' and 'deep' modes).
            wait: Whether to wait for the research run to finish before returning.
            import_sources: When `wait=True`, automatically import discovered sources into the notebook.
            cited_only: When `import_sources=True` on a Deep Research report, import only cited sources.
            timeout: Maximum seconds to wait when `wait=True`.
            allow_duplicate: Whether to allow importing URLs already present in the notebook.
        """
        mode_norm = mode.lower().strip()
        source_norm = source.lower().strip()
        if mode_norm not in ("fast", "deep", "discover", "default", "raw", "curious", "curious_raw"):
            raise ValueError(
                f"Unsupported research mode '{mode}'. Expected 'fast', 'deep', 'discover', 'raw', or 'curious'."
            )
        if source_norm not in ("web", "drive"):
            raise ValueError(f"Unsupported research source '{source}'. Expected 'web' or 'drive'.")

        async with self._open_client() as client:
            if mode_norm in ("discover", "default", "raw", "curious", "curious_raw"):
                disc_mode = "default" if mode_norm == "discover" else mode_norm
                task = await client.research.discover(notebook_id, query, mode=disc_mode)
                serialized = _serialize_research_task(task)
                imported: list[dict[str, Any]] = []
                if import_sources and getattr(task, "sources", None):
                    imported = await self._import_task_sources(
                        client,
                        notebook_id,
                        task,
                        cited_only=cited_only,
                        allow_duplicate=allow_duplicate,
                    )
                return {
                    **serialized,
                    "notebook_id": notebook_id,
                    "mode": mode_norm,
                    "source": "web",
                    "imported_sources": imported,
                    "imported_count": len(imported),
                    "engine": "rpc_bridge",
                }

            started = await client.research.start(
                notebook_id,
                query,
                source=source_norm,
                mode=mode_norm,
            )
            task_id = getattr(started, "task_id", "")
            if not wait:
                return {
                    "status": "in_progress",
                    "notebook_id": notebook_id,
                    "task_id": task_id,
                    "report_id": getattr(started, "report_id", None),
                    "query": query,
                    "mode": mode_norm,
                    "source": source_norm,
                    "engine": "rpc_bridge",
                }

            task = await client.research.wait_for_completion(
                notebook_id,
                task_id,
                timeout=timeout,
            )
            serialized = _serialize_research_task(task)
            imported = []
            if import_sources and getattr(task, "sources", None):
                imported = await self._import_task_sources(
                    client,
                    notebook_id,
                    task,
                    cited_only=cited_only,
                    allow_duplicate=allow_duplicate,
                )
            return {
                **serialized,
                "notebook_id": notebook_id,
                "mode": mode_norm,
                "source": source_norm,
                "imported_sources": imported,
                "imported_count": len(imported),
                "engine": "rpc_bridge",
            }

    async def poll_research(
        self,
        notebook_id: str,
        task_id: str | None = None,
        wait: bool = False,
        timeout: float = 600.0,
        import_sources: bool = False,
        cited_only: bool = False,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Poll or wait for an ongoing research task and optionally import its discovered sources."""
        async with self._open_client() as client:
            if wait:
                task = await client.research.wait_for_completion(
                    notebook_id,
                    task_id,
                    timeout=timeout,
                )
            else:
                task = await client.research.poll(notebook_id, task_id)

            serialized = _serialize_research_task(task)
            imported: list[dict[str, Any]] = []
            if import_sources and getattr(task, "sources", None):
                imported = await self._import_task_sources(
                    client,
                    notebook_id,
                    task,
                    cited_only=cited_only,
                    allow_duplicate=allow_duplicate,
                )
            return {
                **serialized,
                "notebook_id": notebook_id,
                "imported_sources": imported,
                "imported_count": len(imported),
                "engine": "rpc_bridge",
            }

    async def cancel_research(
        self,
        notebook_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        """Cancel an in-flight research run (`client.research.cancel`)."""
        async with self._open_client() as client:
            await client.research.cancel(notebook_id, task_id)
            return {
                "status": "cancelled",
                "notebook_id": notebook_id,
                "task_id": task_id,
                "engine": "rpc_bridge",
            }

    # -------------------------------------------------------------------------
    # Audio Overviews, Studio Artifacts & Persistent Notes
    # -------------------------------------------------------------------------

    async def create_audio_overview(
        self,
        notebook_id: str,
        episode_focus: str = "",
        language_code: str = "en",
        source_ids: list[str] | None = None,
        audio_format: str | None = None,
        audio_length: str | None = None,
        wait: bool = False,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Generate an Audio Overview podcast via `client.artifacts.generate_audio`."""
        async with self._open_client() as client:
            kwargs: dict[str, Any] = {
                "source_ids": source_ids,
                "language": language_code,
                "instructions": episode_focus or None,
            }
            if audio_format:
                from notebooklm._types.enums import AudioFormat

                fmt_map = {
                    "deep-dive": AudioFormat.DEEP_DIVE,
                    "deep_dive": AudioFormat.DEEP_DIVE,
                    "brief": AudioFormat.BRIEF,
                    "critique": AudioFormat.CRITIQUE,
                    "debate": AudioFormat.DEBATE,
                }
                if audio_format.lower() in fmt_map:
                    kwargs["audio_format"] = fmt_map[audio_format.lower()]

            if audio_length:
                from notebooklm._types.enums import AudioLength

                len_map = {
                    "short": AudioLength.SHORT,
                    "default": AudioLength.DEFAULT,
                    "long": AudioLength.LONG,
                }
                if audio_length.lower() in len_map:
                    kwargs["audio_length"] = len_map[audio_length.lower()]

            status = await client.artifacts.generate_audio(notebook_id, **kwargs)
            task_id = getattr(status, "task_id", "")
            if wait and task_id:
                status = await client.artifacts.wait_for_completion(
                    notebook_id,
                    task_id,
                    timeout=timeout,
                )

            state_val = getattr(status, "status", None)
            return {
                "notebookId": notebook_id,
                "taskId": task_id,
                "status": getattr(state_val, "name", str(state_val)).lower() if state_val else "pending",
                "url": getattr(status, "url", None),
                "error": getattr(status, "error", None),
                "engine": "rpc_bridge",
            }

    async def generate_artifact(
        self,
        notebook_id: str,
        artifact_type: str,
        instructions: str = "",
        report_format: str = "briefing_doc",
        language_code: str = "en",
        source_ids: list[str] | None = None,
        wait: bool = False,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Generate a Studio Artifact (report, study_guide, mind_map, quiz, flashcards, slide_deck, data_table, infographic, video)."""
        kind = artifact_type.lower().strip().replace("-", "_")
        async with self._open_client() as client:
            if kind == "mind_map":
                mm = await client.artifacts.generate_mind_map(
                    notebook_id,
                    source_ids=source_ids,
                    language=language_code,
                    instructions=instructions or None,
                )
                return {
                    "notebookId": notebook_id,
                    "artifactType": "mind_map",
                    "noteId": getattr(mm, "note_id", None),
                    "mindMap": getattr(mm, "mind_map", str(mm)),
                    "engine": "rpc_bridge",
                }

            if kind in ("report", "briefing_doc", "study_guide", "blog_post"):
                from notebooklm._types.enums import ReportFormat

                fmt_key = report_format.lower().strip().replace("-", "_")
                if kind in ("briefing_doc", "study_guide", "blog_post"):
                    fmt_key = kind
                fmt_map = {
                    "briefing_doc": ReportFormat.BRIEFING_DOC,
                    "study_guide": ReportFormat.STUDY_GUIDE,
                    "blog_post": ReportFormat.BLOG_POST,
                    "concept_explanation": ReportFormat.CONCEPT_EXPLANATION,
                    "custom": ReportFormat.CUSTOM,
                }
                rfmt = fmt_map.get(fmt_key, ReportFormat.BRIEFING_DOC)
                status = await client.artifacts.generate_report(
                    notebook_id,
                    report_format=rfmt,
                    source_ids=source_ids,
                    language=language_code,
                    custom_prompt=instructions if rfmt == ReportFormat.CUSTOM else None,
                    extra_instructions=instructions if rfmt != ReportFormat.CUSTOM and instructions else None,
                )
            elif kind == "quiz":
                status = await client.artifacts.generate_quiz(
                    notebook_id,
                    source_ids=source_ids,
                    instructions=instructions or None,
                )
            elif kind == "flashcards":
                status = await client.artifacts.generate_flashcards(
                    notebook_id,
                    source_ids=source_ids,
                    instructions=instructions or None,
                )
            elif kind == "slide_deck":
                status = await client.artifacts.generate_slide_deck(
                    notebook_id,
                    source_ids=source_ids,
                    language=language_code,
                    instructions=instructions or None,
                )
            elif kind == "data_table":
                status = await client.artifacts.generate_data_table(
                    notebook_id,
                    source_ids=source_ids,
                    language=language_code,
                    instructions=instructions or None,
                )
            elif kind == "infographic":
                status = await client.artifacts.generate_infographic(
                    notebook_id,
                    source_ids=source_ids,
                    language=language_code,
                    instructions=instructions or None,
                )
            elif kind == "video":
                status = await client.artifacts.generate_video(
                    notebook_id,
                    source_ids=source_ids,
                    language=language_code,
                    instructions=instructions or None,
                )
            elif kind == "audio":
                return await self.create_audio_overview(
                    notebook_id=notebook_id,
                    episode_focus=instructions,
                    language_code=language_code,
                    source_ids=source_ids,
                    wait=wait,
                    timeout=timeout,
                )
            else:
                raise ValueError(
                    f"Unsupported artifact_type '{artifact_type}'. Expected one of: "
                    "'report', 'study_guide', 'briefing_doc', 'blog_post', 'mind_map', "
                    "'quiz', 'flashcards', 'slide_deck', 'data_table', 'infographic', 'video', 'audio'."
                )

            task_id = getattr(status, "task_id", "")
            if wait and task_id:
                status = await client.artifacts.wait_for_completion(
                    notebook_id,
                    task_id,
                    timeout=timeout,
                )
            state_val = getattr(status, "status", None)
            return {
                "notebookId": notebook_id,
                "artifactType": kind,
                "taskId": task_id,
                "status": getattr(state_val, "name", str(state_val)).lower() if state_val else "pending",
                "url": getattr(status, "url", None),
                "error": getattr(status, "error", None),
                "engine": "rpc_bridge",
            }

    async def manage_notes(
        self,
        notebook_id: str,
        action: str = "list",
        title: str = "Agent Note",
        content: str = "",
        note_id: str | None = None,
    ) -> dict[str, Any]:
        """List, get, create, update, or delete persistent notes inside a Gemini Notebook."""
        act = action.lower().strip()
        async with self._open_client() as client:
            if act == "list":
                notes = await client.notes.list(notebook_id)
                return {
                    "notebookId": notebook_id,
                    "notes": [
                        {
                            "noteId": getattr(n, "id", str(n)),
                            "title": getattr(n, "title", ""),
                            "content": getattr(n, "content", ""),
                        }
                        for n in notes
                    ],
                    "engine": "rpc_bridge",
                }
            if act == "get":
                if not note_id:
                    raise ValueError("`note_id` is required when `action == 'get'`.")
                note = await client.notes.get(notebook_id, note_id)
                return {
                    "notebookId": notebook_id,
                    "note": {
                        "noteId": getattr(note, "id", note_id),
                        "title": getattr(note, "title", ""),
                        "content": getattr(note, "content", ""),
                    },
                    "engine": "rpc_bridge",
                }
            if act == "create":
                note = await client.notes.create(notebook_id, title=title, content=content)
                return {
                    "notebookId": notebook_id,
                    "note": {
                        "noteId": getattr(note, "id", ""),
                        "title": getattr(note, "title", title),
                        "content": getattr(note, "content", content),
                    },
                    "engine": "rpc_bridge",
                }
            if act == "update":
                if not note_id:
                    raise ValueError("`note_id` is required when `action == 'update'`.")
                await client.notes.update(notebook_id, note_id, content=content, title=title)
                return {
                    "notebookId": notebook_id,
                    "noteId": note_id,
                    "status": "updated",
                    "engine": "rpc_bridge",
                }
            if act == "delete":
                if not note_id:
                    raise ValueError("`note_id` is required when `action == 'delete'`.")
                await client.notes.delete(notebook_id, note_id)
                return {
                    "notebookId": notebook_id,
                    "deletedNoteId": note_id,
                    "engine": "rpc_bridge",
                }
            raise ValueError(
                f"Unsupported note action '{action}'. Expected 'list', 'get', 'create', 'update', or 'delete'."
            )
