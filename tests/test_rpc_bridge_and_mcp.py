"""Unit tests for Dual-Engine routing, NotebookRpcBridge, Deep Research, and MCP tools."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agy_gemini_notebook.cli import build_parser
from agy_gemini_notebook.config import (
    PluginConfig,
    WorkspaceNotebookBinding,
    load_workspace_binding,
    save_workspace_binding,
)
from agy_gemini_notebook.engines.gcp_enterprise_rest import GcpNotebookEnterpriseClient
from agy_gemini_notebook.engines.rpc_bridge import NotebookRpcBridge
from agy_gemini_notebook.mcp_server import (
    notebook_add_source,
    notebook_attach,
    notebook_create,
    notebook_create_audio,
    notebook_delete,
    notebook_delete_sources,
    notebook_generate_artifact,
    notebook_get,
    notebook_get_source_text,
    notebook_invoke,
    notebook_list,
    notebook_notes,
    notebook_query,
    notebook_rename,
    notebook_research,
    notebook_research_status,
    notebook_search_sources,
    notebook_share,
    notebook_status,
    notebook_sync_files,
)


@dataclass
class FakeNotebook:
    id: str
    title: str
    emoji: str | None = "📓"
    sources_count: int = 2
    is_owner: bool = True
    created_at: Any = None
    modified_at: Any = None


@dataclass
class FakeSource:
    id: str
    title: str
    url: str | None = "https://example.com/spec"
    status: str = "ready"
    kind: str = "web"
    drive_document_id: str | None = None
    word_count: int = 512
    created_at: Any = None


@dataclass
class FakeTopic:
    question: str


@dataclass
class FakeDescription:
    summary: str
    suggested_topics: list[FakeTopic]


@dataclass
class FakeReference:
    source_id: str
    citation_number: int = 1
    cited_text: str = "Grounded passage from spec."
    chunk_id: str = "chunk-1"
    score: float = 0.98
    start_char: int = 10
    end_char: int = 40


@dataclass
class FakeAskResult:
    answer: str
    conversation_id: str = "conv-123"
    turn_number: int = 1
    references: list[FakeReference] | None = None
    next_steps: list[FakeTopic] | None = None


@dataclass
class FakeChunk:
    source_id: str
    text: str
    rank: int = 1
    start: int = 0
    end: int = 50


@dataclass
class FakeResearchStart:
    task_id: str
    report_id: str | None = "rep-1"
    notebook_id: str = "nb-rpc-1"
    query: str = "quantum computing"
    mode: str = "deep"


@dataclass
class FakeResearchSource:
    url: str
    title: str
    result_type: str = "web"
    research_task_id: str | None = "task-deep-1"
    report_markdown: str = ""
    hint: str = "Relevant web source"


@dataclass
class FakeResearchTask:
    task_id: str
    status: str
    query: str
    summary: str
    report: str
    sources: tuple[FakeResearchSource, ...]


@dataclass
class FakeGenStatus:
    task_id: str
    status: str = "completed"
    url: str | None = "https://example.com/audio.m4a"
    error: str | None = None


@dataclass
class FakeNote:
    id: str
    title: str
    content: str


def _make_fake_nlm_client() -> MagicMock:
    client = MagicMock()
    client.notebooks = MagicMock()
    client.notebooks.list = AsyncMock(
        return_value=[
            FakeNotebook(id="nb-rpc-1", title="Architecture Brain"),
            FakeNotebook(id="nb-rpc-2", title="Deep Research Notes"),
        ]
    )
    client.notebooks.get = AsyncMock(
        return_value=FakeNotebook(id="nb-rpc-1", title="Architecture Brain")
    )
    client.notebooks.get_description = AsyncMock(
        return_value=FakeDescription(
            summary="System architecture overview.",
            suggested_topics=[FakeTopic(question="How does Dual-Engine routing work?")],
        )
    )
    client.notebooks.create = AsyncMock(
        side_effect=lambda title: FakeNotebook(id="nb-created", title=title)
    )
    client.notebooks.set_emoji = AsyncMock(
        side_effect=lambda nid, emoji: FakeNotebook(id=nid, title="Created", emoji=emoji)
    )
    client.notebooks.delete = AsyncMock(return_value=None)
    client.notebooks.rename = AsyncMock(
        side_effect=lambda nid, new_title: FakeNotebook(id=nid, title=new_title)
    )
    client.notebooks.get_share_url = MagicMock(
        side_effect=lambda nid: f"https://notebooklm.google.com/notebook/{nid}"
    )

    client.sharing = MagicMock()
    client.sharing.set_public = AsyncMock(return_value=None)

    client.sources = MagicMock()
    client.sources.list = AsyncMock(
        return_value=[
            FakeSource(id="src-1", title="ADR-001.md"),
            FakeSource(id="src-2", title="API Spec.pdf"),
        ]
    )
    client.sources.add_file = AsyncMock(
        side_effect=lambda nid, path, **kw: FakeSource(
            id="src-file-1",
            title=kw.get("title") or Path(path).name,
        )
    )
    client.sources.add_text = AsyncMock(
        side_effect=lambda nid, title, content, **kw: FakeSource(id="src-txt-1", title=title)
    )
    client.sources.add_url = AsyncMock(
        side_effect=lambda nid, url, **kw: FakeSource(
            id="src-url-1",
            title=kw.get("title") or "Web Doc",
            url=url,
        )
    )
    client.sources.add_drive = AsyncMock(
        side_effect=lambda nid, doc_id, title, **kw: FakeSource(
            id="src-drv-1",
            title=title,
            drive_document_id=doc_id,
        )
    )
    client.sources.delete = AsyncMock(return_value=None)
    client.sources.search = AsyncMock(
        return_value=[
            FakeChunk(source_id="src-1", text="Dual-Engine fallback architecture.", rank=1)
        ]
    )
    client.sources.get_fulltext = AsyncMock(
        return_value=MagicMock(
            title="ADR-001.md",
            url=None,
            char_count=34,
            content="Full text of ADR-001 specification",
        )
    )
    client.sources.get_guide = AsyncMock(
        return_value=MagicMock(
            summary="ADR summary",
            keywords=["dual-engine", "mcp"],
        )
    )

    client.chat = MagicMock()
    client.chat.ask = AsyncMock(
        return_value=FakeAskResult(
            answer="The Dual-Engine architecture combines GCP REST and RPC [1].",
            conversation_id="conv-123",
            turn_number=1,
            references=[FakeReference(source_id="src-1")],
            next_steps=[FakeTopic(question="What about Deep Research?")],
        )
    )
    client.chat.save_answer_as_note = AsyncMock(
        return_value=FakeNote(id="note-saved-1", title="Saved Q&A", content="...")
    )

    client.research = MagicMock()
    client.research.start = AsyncMock(
        side_effect=lambda nid, query, source="web", mode="fast": FakeResearchStart(
            task_id="task-deep-1",
            notebook_id=nid,
            query=query,
            mode=mode,
        )
    )
    sample_task = FakeResearchTask(
        task_id="task-deep-1",
        status="completed",
        query="agentic coding harnesses",
        summary="Executive summary of agentic coding harnesses.",
        report="# Deep Research Report\nDetailed analysis of Antigravity 2 and VS Code MCP.",
        sources=(
            FakeResearchSource(
                url="https://example.com/agentic",
                title="Agentic Harnesses Paper",
            ),
        ),
    )
    client.research.wait_for_completion = AsyncMock(return_value=sample_task)
    client.research.poll = AsyncMock(return_value=sample_task)
    client.research.discover = AsyncMock(return_value=sample_task)
    client.research.import_sources_with_verification = AsyncMock(
        return_value=[{"id": "src-imported-1", "title": "Agentic Harnesses Paper"}]
    )
    client.research.select_cited_sources = MagicMock(
        side_effect=lambda sources, report: MagicMock(sources=sources)
    )
    client.research.cancel = AsyncMock(return_value=None)

    client.artifacts = MagicMock()
    client.artifacts.generate_audio = AsyncMock(
        return_value=FakeGenStatus(task_id="art-audio-1", status="in_progress")
    )
    client.artifacts.generate_report = AsyncMock(
        return_value=FakeGenStatus(task_id="art-rep-1", status="completed")
    )
    client.artifacts.generate_quiz = AsyncMock(
        return_value=FakeGenStatus(task_id="art-quiz-1", status="completed")
    )
    client.artifacts.generate_mind_map = AsyncMock(
        return_value=MagicMock(note_id="mm-note-1", mind_map={"root": "Architecture"})
    )
    client.artifacts.wait_for_completion = AsyncMock(
        return_value=FakeGenStatus(
            task_id="art-audio-1",
            status="completed",
            url="https://example.com/audio.m4a",
        )
    )

    client.notes = MagicMock()
    client.notes.list = AsyncMock(
        return_value=[FakeNote(id="note-1", title="Session 1", content="Key decisions")]
    )
    client.notes.get = AsyncMock(
        return_value=FakeNote(id="note-1", title="Session 1", content="Key decisions")
    )
    client.notes.create = AsyncMock(
        side_effect=lambda nid, title="New Note", content="": FakeNote(
            id="note-new", title=title, content=content
        )
    )
    client.notes.update = AsyncMock(return_value=None)
    client.notes.delete = AsyncMock(return_value=None)

    return client


@pytest.fixture()
def mock_rpc_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_client = _make_fake_nlm_client()

    @asynccontextmanager
    async def _fake_open_client(self: NotebookRpcBridge):
        yield fake_client

    monkeypatch.setattr(NotebookRpcBridge, "_open_client", _fake_open_client)
    monkeypatch.setattr(NotebookRpcBridge, "is_available", staticmethod(lambda: True))
    monkeypatch.delenv("GCP_PROJECT_NUMBER", raising=False)
    return fake_client


@pytest.mark.asyncio
async def test_dual_engine_fallback_crud_and_sync(
    tmp_path: Path,
    mock_rpc_client: MagicMock,
) -> None:
    """When GCP_PROJECT_NUMBER is unset, all MCP CRUD & sync tools fall back to NotebookRpcBridge."""
    status = await notebook_status(workspace_dir=str(tmp_path))
    assert status["gcp_enterprise_rest"]["configured"] is False
    assert status["rpc_bridge"]["notebooklm_py_installed"] is True

    # 1. Create notebook via RPC fallback and auto-attach with alias
    created = await notebook_create(
        title="My Consumer Notebook",
        attach_to_workspace=True,
        alias="main",
        emoji="🚀",
        workspace_dir=str(tmp_path),
    )
    assert created["engine"] == "rpc_bridge"
    assert created["notebookId"] == "nb-created"
    assert created["attached_to_workspace"] is True

    binding = load_workspace_binding(tmp_path)
    assert binding is not None
    assert binding.notebook_id == "nb-created"
    assert binding.aliases["main"] == "nb-created"

    # 2. List & filter notebooks via RPC fallback
    listed = await notebook_list(query="Architecture", workspace_dir=str(tmp_path))
    assert listed["engine"] == "rpc_bridge"
    assert listed["count"] == 1
    assert listed["notebooks"][0]["notebookId"] == "nb-rpc-1"

    # 3. Get notebook details (metadata + AI summary + sources) via alias
    details = await notebook_get(notebook_id="main", workspace_dir=str(tmp_path))
    assert details["engine"] == "rpc_bridge"
    assert details["summary"] == "System architecture overview."
    assert len(details["sources"]) == 2

    # 4. Sync local file via RPC fallback
    sample_doc = tmp_path / "SPEC.md"
    sample_doc.write_text("# Specification\nDual-Engine details.", encoding="utf-8")
    synced = await notebook_sync_files(
        file_paths=[str(sample_doc)],
        wait=True,
        workspace_dir=str(tmp_path),
    )
    assert synced["engine"] == "rpc_bridge"
    assert synced["uploaded"][0]["result"]["source"]["title"] == "SPEC.md"

    # 5. Add text, web, and gdrive sources via RPC fallback
    added_txt = await notebook_add_source(
        source_type="text",
        value="Inline notes",
        title="Notes.txt",
        workspace_dir=str(tmp_path),
    )
    assert added_txt["engine"] == "rpc_bridge"
    assert added_txt["source"]["sourceId"] == "src-txt-1"

    added_web = await notebook_add_source(
        source_type="web",
        value="https://example.com/doc",
        title="Example Doc",
        workspace_dir=str(tmp_path),
    )
    assert added_web["source"]["sourceId"] == "src-url-1"

    added_drv = await notebook_add_source(
        source_type="gdrive",
        value="1a2b3c4d",
        title="Drive Spec",
        workspace_dir=str(tmp_path),
    )
    assert added_drv["source"]["driveDocumentId"] == "1a2b3c4d"

    # 6. Delete sources, rename, share, and delete notebook via RPC fallback
    del_src = await notebook_delete_sources(
        source_ids=["src-1"],
        workspace_dir=str(tmp_path),
    )
    assert del_src["deletedSourceIds"] == ["src-1"]

    renamed = await notebook_rename(
        new_title="Renamed Brain",
        workspace_dir=str(tmp_path),
    )
    assert renamed["title"] == "Renamed Brain"
    assert load_workspace_binding(tmp_path).title == "Renamed Brain"

    shared = await notebook_share(public=True, workspace_dir=str(tmp_path))
    assert "https://notebooklm.google.com/notebook/" in shared["shareUrl"]

    deleted = await notebook_delete(
        notebook_ids=["nb-created"],
        workspace_dir=str(tmp_path),
    )
    assert deleted["deleted"] == ["nb-created"]


@pytest.mark.asyncio
async def test_invoke_query_search_and_deep_research(
    tmp_path: Path,
    mock_rpc_client: MagicMock,
) -> None:
    """Test invoking notebooks by title/alias, grounded Q&A, passage search, and Deep Research."""
    await notebook_attach(
        notebook_id="nb-rpc-1",
        title="Architecture Brain",
        alias="arch",
        active_source_ids=["src-1"],
        workspace_dir=str(tmp_path),
    )

    # 1. Invoke by fuzzy title with a grounded question
    invoked = await notebook_invoke(
        target="Architecture Brain",
        question="How does Dual-Engine work?",
        workspace_dir=str(tmp_path),
    )
    assert invoked["invoked_notebook_id"] == "nb-rpc-1"
    assert "Dual-Engine architecture" in invoked["query_result"]["answer"]
    assert invoked["query_result"]["citations"][0]["source_id"] == "src-1"

    # 2. Query with save_as_note=True
    qa = await notebook_query(
        question="Summarize ADR-001",
        save_as_note=True,
        note_title="ADR Summary",
        workspace_dir=str(tmp_path),
    )
    assert qa["status"] == "ok"
    assert qa["saved_note"]["id"] == "note-saved-1"

    # 3. Passage search across sources
    passages = await notebook_search_sources(
        query="Dual-Engine",
        workspace_dir=str(tmp_path),
    )
    assert passages["count"] == 1
    assert passages["matches"][0]["source_id"] == "src-1"

    # 4. Fulltext + AI source guide
    ft = await notebook_get_source_text(
        source_id="src-1",
        include_guide=True,
        workspace_dir=str(tmp_path),
    )
    assert "Full text of ADR-001" in ft["content"]
    assert ft["guide"]["keywords"] == ["dual-engine", "mcp"]

    # 5. Deep Research (with new notebook creation + auto-import of cited sources)
    deep = await notebook_research(
        query="agentic coding harnesses",
        mode="deep",
        source="web",
        wait=True,
        import_sources=True,
        cited_only=True,
        create_notebook_title="Agentic Research 2026",
        workspace_dir=str(tmp_path),
    )
    assert deep["mode"] == "deep"
    assert "# Deep Research Report" in deep["report"]
    assert deep["imported_count"] == 1
    assert deep["created_notebook"]["notebookId"] == "nb-created"

    # 6. Poll & cancel research status
    polled = await notebook_research_status(
        task_id="task-deep-1",
        wait=False,
        import_sources=True,
        workspace_dir=str(tmp_path),
    )
    assert polled["task_id"] == "task-deep-1"
    assert polled["imported_count"] == 1

    cancelled = await notebook_research_status(
        task_id="task-deep-1",
        cancel=True,
        workspace_dir=str(tmp_path),
    )
    assert cancelled["status"] == "cancelled"

    # 7. Audio overview, Studio artifacts & persistent notes
    audio = await notebook_create_audio(
        episode_focus="Focus on MCP integration",
        audio_format="deep-dive",
        audio_length="default",
        wait=True,
        workspace_dir=str(tmp_path),
    )
    assert audio["engine"] == "rpc_bridge"
    assert audio["status"] == "completed"

    mind_map = await notebook_generate_artifact(
        artifact_type="mind_map",
        instructions="Map the Dual-Engine components",
        workspace_dir=str(tmp_path),
    )
    assert mind_map["artifactType"] == "mind_map"
    assert mind_map["noteId"] == "mm-note-1"

    study_guide = await notebook_generate_artifact(
        artifact_type="study_guide",
        wait=True,
        workspace_dir=str(tmp_path),
    )
    assert study_guide["artifactType"] == "study_guide"

    note_created = await notebook_notes(
        action="create",
        title="Decision Log",
        content="Selected Dual-Engine fallback.",
        workspace_dir=str(tmp_path),
    )
    assert note_created["note"]["title"] == "Decision Log"

    note_list = await notebook_notes(action="list", workspace_dir=str(tmp_path))
    assert len(note_list["notes"]) == 1


@pytest.mark.asyncio
async def test_gcp_enterprise_query_and_mock_transport(tmp_path: Path) -> None:
    """Verify GcpNotebookEnterpriseClient REST endpoints and Enterprise Grounded Q&A via MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(":answer"):
            return httpx.Response(
                200,
                json={
                    "answer": {
                        "answerText": "Enterprise grounded answer from Discovery Engine.",
                        "citations": [{"startIndex": 0, "endIndex": 20}],
                        "references": [{"chunkInfo": {"content": "Enterprise doc chunk"}}],
                    }
                },
            )
        if path.endswith("/notebooks") and request.method == "POST":
            return httpx.Response(
                200,
                json={"name": "projects/123/locations/global/notebooks/nb-ent-1", "notebookId": "nb-ent-1", "title": "Enterprise NB"},
            )
        return httpx.Response(200, json={})

    cfg = PluginConfig(
        gcp_project_number="123456789",
        gcp_location="global",
        gcp_endpoint_location="global",
        gcp_access_token="mock-oauth-token",
        gcp_engine_id="ent-search-engine",
    )
    client = GcpNotebookEnterpriseClient(
        config=cfg,
        transport=httpx.MockTransport(handler),
    )
    nb = await client.create_notebook("Enterprise NB")
    assert nb["notebookId"] == "nb-ent-1"

    ans = await client.query_enterprise("What is the enterprise policy?")
    assert ans["engine"] == "gcp_enterprise_rest"
    assert ans["answer"] == "Enterprise grounded answer from Discovery Engine."


def test_cli_parser_commands() -> None:
    """Ensure the `agy-gemini-notebook` CLI parser accepts all key subcommands for IDE/terminal harnesses."""
    parser = build_parser()

    args_res = parser.parse_args(
        ["research", "Model Context Protocol", "--mode", "deep", "--new-notebook", "MCP Deep Dive"]
    )
    assert args_res.command == "research"
    assert args_res.mode == "deep"
    assert args_res.new_notebook == "MCP Deep Dive"

    args_inv = parser.parse_args(
        ["invoke", "--target", "arch", "--question", "Explain the auth flow"]
    )
    assert args_inv.command == "invoke"
    assert args_inv.target == "arch"
    assert args_inv.question == "Explain the auth flow"

    args_binding = WorkspaceNotebookBinding(
        notebook_id="nb-1",
        aliases={"arch": "nb-1"},
    )
    assert args_binding.aliases["arch"] == "nb-1"
    _ = save_workspace_binding


@pytest.mark.asyncio
async def test_mcp_resources_prompts_and_cli_dispatch(
    tmp_path: Path,
    mock_rpc_client: MagicMock,
) -> None:
    """Verify MCP resources, MCP prompts, and CLI _dispatch execution."""
    import json as _json

    from agy_gemini_notebook.cli import _dispatch
    from agy_gemini_notebook.mcp_server import (
        prompt_deep_research,
        prompt_invoke_notebook,
        resource_active_notebook,
        resource_notebook_by_target,
        resource_notebook_status,
    )

    parser = build_parser()
    args_attach = parser.parse_args(
        ["-w", str(tmp_path), "attach", "nb-rpc-1", "-t", "Architecture Brain", "-a", "arch"]
    )
    res_attach = await _dispatch(args_attach)
    assert res_attach["status"] == "attached"

    args_query = parser.parse_args(
        ["-w", str(tmp_path), "query", "How does routing work?"]
    )
    res_query = await _dispatch(args_query)
    assert res_query["status"] == "ok"

    status_json = _json.loads(await resource_notebook_status())
    assert "gcp_enterprise_rest" in status_json

    nb_target_json = _json.loads(await resource_notebook_by_target("nb-rpc-1"))
    assert nb_target_json["notebookId"] == "nb-rpc-1"
    _ = resource_active_notebook

    p1 = prompt_invoke_notebook(target="arch", question="Explain Dual-Engine")
    assert "notebook_invoke" in p1
    assert "Explain Dual-Engine" in p1

    p2 = prompt_deep_research(topic="MCP Sidecars", notebook_title="Sidecar Research")
    assert "notebook_research" in p2
    assert "Sidecar Research" in p2


@pytest.mark.asyncio
async def test_auth_required_graceful_response_and_login_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that missing RPC session returns structured `status == 'auth_required'` instead of crashing MCP."""
    from agy_gemini_notebook.engines.rpc_bridge import NotebookAuthRequiredError
    from agy_gemini_notebook.mcp_server import notebook_login

    @asynccontextmanager
    async def _unauth_open_client(self: NotebookRpcBridge):
        raise NotebookAuthRequiredError("No authenticated Gemini Notebook session found.")
        yield  # pragma: no cover

    monkeypatch.setattr(NotebookRpcBridge, "_open_client", _unauth_open_client)
    monkeypatch.delenv("GCP_PROJECT_NUMBER", raising=False)

    created = await notebook_create(
        title="Should Return Auth Required",
        workspace_dir=str(tmp_path),
    )
    assert created["status"] == "auth_required"
    assert created["authenticated"] is False
    assert "notebook_login" in created["remediation"]
    assert "compliance_notice" in created

    listed = await notebook_list(workspace_dir=str(tmp_path))
    assert listed["status"] == "auth_required"

    async def _fake_login(
        self: NotebookRpcBridge,
        browser: str = "chromium",
        timeout: int = 300,
        browser_cookies: str | None = "auto",
        account: str | None = None,
        fresh: bool = False,
        gcloud: bool = False,
        open_system_browser: bool = False,
    ) -> dict[str, Any]:
        return {
            "status": "authenticated",
            "authenticated": True,
            "engine": "rpc_bridge",
            "profile": "default",
            "browser_cookies": browser_cookies or "auto",
            "compliance_notice": {"grey_area_engine": "rpc_bridge"},
        }

    monkeypatch.setattr(NotebookRpcBridge, "login", _fake_login)
    login_res = await notebook_login(browser_cookies="auto", timeout=120)
    assert login_res["status"] == "authenticated"
    assert login_res["authenticated"] is True
    assert login_res["browser_cookies"] == "auto"
    assert "compliance_notice" in login_res


@pytest.mark.asyncio
async def test_rpc_bridge_login_modes_never_use_playwright(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify NotebookRpcBridge.login uses --browser-cookies or webbrowser.open and never launches Playwright."""
    import webbrowser

    bridge = NotebookRpcBridge()
    opened_urls: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, new=2: opened_urls.append(url) or True)

    open_res = await bridge.login(open_system_browser=True)
    assert open_res["status"] == "browser_opened"
    assert opened_urls == ["https://notebooklm.google.com/"]
    assert "compliance_notice" in open_res

    captured_cmds: list[tuple[str, ...]] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"Authentication saved", b"")

        def kill(self) -> None:
            pass

    async def _fake_exec(*cmd: str, **kwargs: Any) -> _FakeProc:
        captured_cmds.append(cmd)
        if "--storage" in cmd:
            idx = cmd.index("--storage")
            storage_file = Path(cmd[idx + 1])
            storage_file.parent.mkdir(parents=True, exist_ok=True)
            storage_file.write_text('{"cookies": []}', encoding="utf-8")
        return _FakeProc()

    import asyncio as _asyncio

    import agy_gemini_notebook.engines.rpc_bridge as _rpc_mod

    monkeypatch.setattr(_rpc_mod, "FALLBACK_AUTH_DIR", tmp_path / ".notebooklm-auth")
    monkeypatch.setattr(_asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(NotebookRpcBridge, "has_saved_session", lambda self: True)

    cookie_res = await bridge.login(browser_cookies="brave", account="0")
    assert cookie_res["status"] == "authenticated"
    assert cookie_res["browser_cookies"] == "brave"
    assert any("--browser-cookies" in c and "brave" in c for c in captured_cmds)
    assert all("playwright" not in " ".join(c).lower() for c in captured_cmds)


@pytest.mark.asyncio
async def test_mcp_stdio_server_handshake() -> None:
    """Verify the portable `scripts/run_mcp.sh` launcher over live MCP stdio JSON-RPC."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    repo_root = Path(__file__).resolve().parent.parent
    params = StdioServerParameters(
        command="bash",
        args=[str(repo_root / "scripts" / "run_mcp.sh")],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.server_info.name == "gemini_notebook_mcp"

        tools = await session.list_tools()
        assert len(tools.tools) == 21

        prompts = await session.list_prompts()
        assert len(prompts.prompts) == 2

        resources = await session.list_resources()
        assert len(resources.resources) == 2

        status_res = await session.call_tool("notebook_status", {})
        assert not status_res.is_error
