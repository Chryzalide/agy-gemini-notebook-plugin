"""Unit tests for workspace notebook bindings and Official GCP v1alpha REST Client (`GcpNotebookEnterpriseClient`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import agy_gemini_notebook.mcp_server as mcp_mod
from agy_gemini_notebook.config import (
    PluginConfig,
    WorkspaceNotebookBinding,
    describe_environment,
    load_workspace_binding,
    resolve_workspace_dir,
    save_workspace_binding,
)
from agy_gemini_notebook.engines.gcp_enterprise_rest import GcpNotebookEnterpriseClient
from agy_gemini_notebook.mcp_server import (
    notebook_add_source,
    notebook_create,
    notebook_create_audio,
    notebook_delete,
    notebook_delete_sources,
    notebook_get,
    notebook_list,
    notebook_query,
    notebook_share,
    notebook_sync_files,
)


def test_workspace_binding_roundtrip(tmp_path: Path) -> None:
    binding = WorkspaceNotebookBinding(
        notebook_id="nb-12345",
        title="Architecture Specs",
        engine="gcp_rest",
        active_source_ids=["src-1", "src-2"],
        aliases={"arch": "nb-12345", "research": "nb-67890"},
        gcp_project_number="99887766",
        gcp_location="eu",
        gcp_endpoint_location="eu",
        gcp_engine_id="ent-engine-01",
    )
    saved = save_workspace_binding(binding, workspace_dir=tmp_path)
    assert saved.is_file()

    loaded = load_workspace_binding(workspace_dir=tmp_path)
    assert loaded is not None
    assert loaded.notebook_id == "nb-12345"
    assert loaded.title == "Architecture Specs"
    assert loaded.engine == "gcp_rest"
    assert loaded.active_source_ids == ["src-1", "src-2"]
    assert loaded.aliases == {"arch": "nb-12345", "research": "nb-67890"}
    assert loaded.gcp_project_number == "99887766"
    assert loaded.gcp_location == "eu"
    assert loaded.gcp_endpoint_location == "eu"
    assert loaded.gcp_engine_id == "ent-engine-01"


def test_resolve_workspace_dir_and_describe_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGY_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("GCP_PROJECT_NUMBER", "11223344")
    monkeypatch.setenv("GCP_ACCESS_TOKEN", "test-oauth-token")

    resolved = resolve_workspace_dir()
    assert resolved == tmp_path.resolve()

    env_info = describe_environment(workspace_dir=tmp_path)
    assert env_info["workspace_dir"] == str(tmp_path)
    assert env_info["attached_notebook"] is None
    assert env_info["gcp_enterprise_rest"]["configured"] is True
    assert env_info["gcp_enterprise_rest"]["project_number"] == "11223344"
    assert env_info["gcp_enterprise_rest"]["gcloud_authenticated"] is True


def test_get_gcloud_token_env_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = PluginConfig(gcp_access_token="  direct-token-123  ")
    assert cfg.get_gcloud_token() == "direct-token-123"

    def _fail_run(*args: Any, **kwargs: Any) -> Any:
        raise OSError("gcloud binary not found")

    monkeypatch.delenv("GCP_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr("subprocess.run", _fail_run)
    cfg_no_token = PluginConfig(gcp_access_token=None)
    with pytest.raises(RuntimeError, match="Unable to obtain Google Cloud access token"):
        cfg_no_token.get_gcloud_token()


def test_gcp_client_urls_and_missing_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GCP_PROJECT_NUMBER", raising=False)
    cfg = PluginConfig(
        gcp_project_number="123456789",
        gcp_location="eu",
        gcp_endpoint_location="eu",
        gcp_access_token="fake-token",
    )
    client = GcpNotebookEnterpriseClient(config=cfg)
    assert (
        client.base_url
        == "https://eu-discoveryengine.googleapis.com/v1alpha/projects/123456789/locations/eu"
    )
    assert (
        client.upload_base_url
        == "https://eu-discoveryengine.googleapis.com/upload/v1alpha/projects/123456789/locations/eu"
    )

    missing_client = GcpNotebookEnterpriseClient(config=PluginConfig(gcp_project_number=None))
    with pytest.raises(ValueError, match="GCP_PROJECT_NUMBER is required"):
        _ = missing_client.base_url


@pytest.mark.asyncio
async def test_notebooks_crud_and_sharing() -> None:
    """Verify create_notebook, get_notebook, list_recently_viewed, batch_delete_notebooks, and share_notebook."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.headers["Authorization"] == "Bearer mock-token"
        path = request.url.path

        if request.method == "POST" and path.endswith("/notebooks"):
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "name": "projects/123456789/locations/global/notebooks/nb-101",
                    "notebookId": "nb-101",
                    "title": body["title"],
                },
            )
        if request.method == "GET" and path.endswith("/notebooks/nb-101"):
            return httpx.Response(
                200,
                json={
                    "name": "projects/123456789/locations/global/notebooks/nb-101",
                    "notebookId": "nb-101",
                    "title": "Architecture Brain",
                    "sources": [{"sourceId": {"id": "src-1"}, "title": "Spec.md"}],
                },
            )
        if request.method == "GET" and path.endswith("/notebooks:listRecentlyViewed"):
            assert request.url.params.get("pageSize") == "20"
            return httpx.Response(
                200,
                json={
                    "notebooks": [
                        {"notebookId": "nb-101", "title": "Architecture Brain"},
                        {"notebookId": "nb-102", "title": "Security Review"},
                    ]
                },
            )
        if request.method == "POST" and path.endswith("/notebooks:batchDelete"):
            body = json.loads(request.content.decode("utf-8"))
            assert body == {
                "names": [
                    "projects/123456789/locations/global/notebooks/nb-101",
                    "projects/123456789/locations/global/notebooks/nb-102",
                ]
            }
            return httpx.Response(200, json={})
        if request.method == "POST" and path.endswith("/notebooks/nb-101:share"):
            body = json.loads(request.content.decode("utf-8"))
            assert body == {
                "accountAndRoles": [{"email": "alice@example.com", "role": "PROJECT_ROLE_WRITER"}],
                "notifyViaEmail": False,
            }
            return httpx.Response(200, json={"shared": True})

        return httpx.Response(404, json={"error": f"Unhandled route: {request.method} {path}"})

    cfg = PluginConfig(
        gcp_project_number="123456789",
        gcp_location="global",
        gcp_endpoint_location="global",
        gcp_access_token="mock-token",
    )
    client = GcpNotebookEnterpriseClient(config=cfg, transport=httpx.MockTransport(handler))

    created = await client.create_notebook("Architecture Brain")
    assert created["notebookId"] == "nb-101"
    assert created["title"] == "Architecture Brain"

    fetched = await client.get_notebook("nb-101")
    assert fetched["notebookId"] == "nb-101"
    assert len(fetched["sources"]) == 1

    recent = await client.list_recently_viewed(page_size=20)
    assert len(recent) == 2
    assert recent[0]["notebookId"] == "nb-101"

    deleted = await client.batch_delete_notebooks(
        ["nb-101", "projects/123456789/locations/global/notebooks/nb-102"]
    )
    assert deleted == {}

    shared = await client.share_notebook(
        notebook_id="nb-101",
        email="alice@example.com",
        role="PROJECT_ROLE_WRITER",
        notify_via_email=False,
    )
    assert shared == {"shared": True}
    assert len(captured) == 5


@pytest.mark.asyncio
async def test_sources_batch_create_get_and_delete() -> None:
    """Verify batch_create_sources (text, web, YouTube, Google Drive), get_source, and batch_delete_sources."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/notebooks/nb-101/sources:batchCreate"):
            body = json.loads(request.content.decode("utf-8"))
            contents = body["userContents"]
            assert len(contents) == 4
            assert contents[0] == {"textContent": {"sourceName": "Notes", "content": "Hello world"}}
            assert contents[1] == {
                "webContent": {"url": "https://example.com/docs", "sourceName": "Web Docs"}
            }
            assert contents[2] == {
                "videoContent": {"youtubeUrl": "https://www.youtube.com/watch?v=abc123xyz"}
            }
            assert contents[3] == {
                "googleDriveContent": {
                    "documentId": "1DriveDocId",
                    "mimeType": "application/vnd.google-apps.document",
                    "sourceName": "Architecture Spec",
                }
            }
            return httpx.Response(
                200,
                json={
                    "sources": [
                        {"sourceId": {"id": "src-text"}},
                        {"sourceId": {"id": "src-web"}},
                        {"sourceId": {"id": "src-yt"}},
                        {"sourceId": {"id": "src-drive"}},
                    ]
                },
            )
        if request.method == "GET" and path.endswith("/notebooks/nb-101/sources/src-text"):
            return httpx.Response(
                200,
                json={
                    "name": "projects/123456789/locations/global/notebooks/nb-101/sources/src-text",
                    "title": "Notes",
                    "metadata": {"wordCount": 120, "tokenCount": 160},
                    "status": "SOURCE_STATUS_COMPLETE",
                },
            )
        if request.method == "POST" and path.endswith("/notebooks/nb-101/sources:batchDelete"):
            body = json.loads(request.content.decode("utf-8"))
            assert body == {
                "names": [
                    "projects/123456789/locations/global/notebooks/nb-101/sources/src-text",
                    "projects/123456789/locations/global/notebooks/nb-101/sources/src-web",
                ]
            }
            return httpx.Response(200, json={})

        return httpx.Response(404, json={"error": path})

    cfg = PluginConfig(
        gcp_project_number="123456789",
        gcp_location="global",
        gcp_endpoint_location="global",
        gcp_access_token="mock-token",
    )
    client = GcpNotebookEnterpriseClient(config=cfg, transport=httpx.MockTransport(handler))

    batch_res = await client.batch_create_sources(
        notebook_id="nb-101",
        user_contents=[
            {"textContent": {"sourceName": "Notes", "content": "Hello world"}},
            {"webContent": {"url": "https://example.com/docs", "sourceName": "Web Docs"}},
            {"videoContent": {"youtubeUrl": "https://www.youtube.com/watch?v=abc123xyz"}},
            {
                "googleDriveContent": {
                    "documentId": "1DriveDocId",
                    "mimeType": "application/vnd.google-apps.document",
                    "sourceName": "Architecture Spec",
                }
            },
        ],
    )
    assert len(batch_res["sources"]) == 4

    src_info = await client.get_source("nb-101", "src-text")
    assert src_info["status"] == "SOURCE_STATUS_COMPLETE"
    assert src_info["metadata"]["wordCount"] == 120

    del_res = await client.batch_delete_sources(
        notebook_id="nb-101",
        source_ids=[
            "src-text",
            "projects/123456789/locations/global/notebooks/nb-101/sources/src-web",
        ],
    )
    assert del_res == {}


@pytest.mark.asyncio
async def test_upload_file_source_headers_and_mime_types(tmp_path: Path) -> None:
    """Verify upload_file_source URL (/upload/v1alpha/.../sources:uploadFile), raw headers, and MIME resolution."""
    uploaded_requests: list[tuple[str, dict[str, str], bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        uploaded_requests.append(( str(request.url), dict(request.headers), request.content ))
        return httpx.Response(
            200,
            json={"sourceId": {"id": f"src-{len(uploaded_requests)}"}},
        )

    cfg = PluginConfig(
        gcp_project_number="123456789",
        gcp_location="us",
        gcp_endpoint_location="us",
        gcp_access_token="mock-token",
    )
    client = GcpNotebookEnterpriseClient(config=cfg, transport=httpx.MockTransport(handler))

    cases = [
        ("architecture.md", b"# Architecture\n", "text/markdown", None),
        ("manual.pdf", b"%PDF-1.7...", "application/pdf", "CustomManual.pdf"),
        (
            "slides.pptx",
            b"PK\x03\x04pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            None,
        ),
        ("overview.mp3", b"ID3...", "audio/mpeg", None),
        ("diagram.png", b"\x89PNG...", "image/png", None),
    ]

    for filename, payload_bytes, expected_mime, custom_name in cases:
        fpath = tmp_path / filename
        fpath.write_bytes(payload_bytes)
        res = await client.upload_file_source(
            notebook_id="nb-upload-1",
            file_path=fpath,
            display_name=custom_name,
        )
        assert "sourceId" in res

    assert len(uploaded_requests) == len(cases)
    for idx, (filename, payload_bytes, expected_mime, custom_name) in enumerate(cases):
        url, headers, content = uploaded_requests[idx]
        assert (
            url
            == "https://us-discoveryengine.googleapis.com/upload/v1alpha/projects/123456789/locations/us/notebooks/nb-upload-1/sources:uploadFile"
        )
        assert headers["x-goog-upload-protocol"] == "raw"
        assert headers["x-goog-upload-file-name"] == (custom_name or filename)
        assert headers["content-type"] == expected_mime
        assert headers["authorization"] == "Bearer mock-token"
        assert content == payload_bytes

    with pytest.raises(FileNotFoundError):
        await client.upload_file_source("nb-upload-1", tmp_path / "does-not-exist.pdf")


@pytest.mark.asyncio
async def test_audio_overviews_and_enterprise_qa() -> None:
    """Verify create_audio_overview, delete_audio_overview, and query_enterprise."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/notebooks/nb-101/audioOverviews"):
            body = json.loads(request.content.decode("utf-8"))
            assert body == {
                "generationOptions": {
                    "languageCode": "fr",
                    "episodeFocus": "Focus on security architecture",
                    "sourceIds": [{"id": "src-1"}, {"id": "src-2"}],
                }
            }
            return httpx.Response(
                200,
                json={
                    "name": "projects/123456789/locations/global/notebooks/nb-101/audioOverviews/default",
                    "status": "AUDIO_OVERVIEW_STATUS_IN_PROGRESS",
                },
            )
        if request.method == "DELETE" and path.endswith(
            "/notebooks/nb-101/audioOverviews/default"
        ):
            return httpx.Response(200, json={})
        if request.method == "POST" and path.endswith(":answer"):
            body = json.loads(request.content.decode("utf-8"))
            assert body["query"]["text"] == "How is IAM configured?"
            assert body["session"] == "sess-99"
            return httpx.Response(
                200,
                json={
                    "answer": {
                        "answerText": "IAM requires roles/discoveryengine.notebooklmUser.",
                        "citations": [{"startIndex": 0, "endIndex": 49}],
                        "references": [{"chunkInfo": {"content": "IAM role documentation"}}],
                    }
                },
            )
        return httpx.Response(404, json={"error": path})

    cfg = PluginConfig(
        gcp_project_number="123456789",
        gcp_location="global",
        gcp_endpoint_location="global",
        gcp_access_token="mock-token",
        gcp_engine_id="ent-engine-1",
    )
    client = GcpNotebookEnterpriseClient(config=cfg, transport=httpx.MockTransport(handler))

    audio = await client.create_audio_overview(
        notebook_id="nb-101",
        episode_focus="Focus on security architecture",
        language_code="fr",
        source_ids=["src-1", "src-2"],
    )
    assert audio["status"] == "AUDIO_OVERVIEW_STATUS_IN_PROGRESS"

    deleted_audio = await client.delete_audio_overview("nb-101", "default")
    assert deleted_audio == {}

    qa = await client.query_enterprise("How is IAM configured?", session_id="sess-99")
    assert qa["status"] == "ok"
    assert qa["engine"] == "gcp_enterprise_rest"
    assert "roles/discoveryengine.notebooklmUser" in qa["answer"]

    client_no_engine = GcpNotebookEnterpriseClient(
        config=PluginConfig(
            gcp_project_number="123456789",
            gcp_access_token="mock-token",
            gcp_engine_id=None,
        ),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="GCP_ENGINE_ID is required"):
        await client_no_engine.query_enterprise("Question without engine_id")


@pytest.mark.asyncio
async def test_mcp_tools_gcp_enterprise_rest_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify MCP tools route through GcpNotebookEnterpriseClient when GCP_PROJECT_NUMBER is configured."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/notebooks"):
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"notebookId": "nb-gcp-1", "title": body["title"]},
            )
        if request.method == "GET" and path.endswith("/notebooks:listRecentlyViewed"):
            return httpx.Response(
                200,
                json={"notebooks": [{"notebookId": "nb-gcp-1", "title": "GCP Enterprise NB"}]},
            )
        if request.method == "GET" and path.endswith("/notebooks/nb-gcp-1"):
            return httpx.Response(
                200,
                json={
                    "notebookId": "nb-gcp-1",
                    "title": "GCP Enterprise NB",
                    "sources": [{"sourceId": {"id": "src-1"}, "title": "README.md"}],
                },
            )
        if request.method == "POST" and path.endswith("/sources:batchCreate"):
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"sources": body["userContents"]})
        if request.method == "POST" and path.endswith("/sources:uploadFile"):
            return httpx.Response(
                200,
                json={"sourceId": {"id": "src-uploaded"}},
            )
        if request.method == "POST" and path.endswith("/sources:batchDelete"):
            return httpx.Response(200, json={})
        if request.method == "POST" and path.endswith(":share"):
            return httpx.Response(200, json={"shared": True})
        if request.method == "POST" and path.endswith("/audioOverviews"):
            return httpx.Response(200, json={"status": "AUDIO_OVERVIEW_STATUS_IN_PROGRESS"})
        if request.method == "POST" and path.endswith(":answer"):
            return httpx.Response(
                200,
                json={"answer": {"answerText": "Enterprise REST answer", "citations": []}},
            )
        if request.method == "POST" and path.endswith("/notebooks:batchDelete"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": path})

    mock_transport = httpx.MockTransport(handler)
    orig_init = GcpNotebookEnterpriseClient.__init__

    def _patched_init(self: GcpNotebookEnterpriseClient, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("transport", mock_transport)
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(mcp_mod.GcpNotebookEnterpriseClient, "__init__", _patched_init)
    monkeypatch.setenv("GCP_PROJECT_NUMBER", "123456789")
    monkeypatch.setenv("GCP_ACCESS_TOKEN", "mock-token")
    monkeypatch.setenv("GCP_ENGINE_ID", "ent-engine-1")

    created = await notebook_create(
        title="GCP Enterprise NB",
        attach_to_workspace=True,
        workspace_dir=str(tmp_path),
    )
    assert created["engine"] == "gcp_enterprise_rest"
    assert created["notebookId"] == "nb-gcp-1"

    listed = await notebook_list(workspace_dir=str(tmp_path))
    assert listed["engine"] == "gcp_enterprise_rest"
    assert listed["count"] == 1

    fetched = await notebook_get(workspace_dir=str(tmp_path))
    assert fetched["engine"] == "gcp_enterprise_rest"

    for stype, val in [
        ("text", "Inline enterprise note"),
        ("web", "https://cloud.google.com/docs"),
        ("youtube", "https://www.youtube.com/watch?v=123"),
        ("gdrive", "drive-doc-id"),
    ]:
        added = await notebook_add_source(
            source_type=stype,
            value=val,
            title=f"Source {stype}",
            workspace_dir=str(tmp_path),
        )
        assert added["engine"] == "gcp_enterprise_rest"

    with pytest.raises(ValueError, match="Unsupported source_type"):
        await notebook_add_source(
            source_type="invalid_type",
            value="x",
            workspace_dir=str(tmp_path),
        )

    doc_file = tmp_path / "README.md"
    doc_file.write_text("# Enterprise Readme\n", encoding="utf-8")
    synced = await notebook_sync_files(
        file_paths=[str(doc_file)],
        workspace_dir=str(tmp_path),
    )
    assert synced["engine"] == "gcp_enterprise_rest"
    assert synced["uploaded"][0]["result"]["sourceId"]["id"] == "src-uploaded"

    del_src = await notebook_delete_sources(
        source_ids=["src-1"],
        workspace_dir=str(tmp_path),
    )
    assert del_src["engine"] == "gcp_enterprise_rest"

    shared = await notebook_share(
        email="colleague@example.com",
        role="PROJECT_ROLE_READER",
        workspace_dir=str(tmp_path),
    )
    assert shared["engine"] == "gcp_enterprise_rest"

    audio = await notebook_create_audio(
        episode_focus="Enterprise summary",
        workspace_dir=str(tmp_path),
    )
    assert audio["engine"] == "gcp_enterprise_rest"

    qa = await notebook_query(
        question="What is in the enterprise notebook?",
        engine="gcp_rest",
        workspace_dir=str(tmp_path),
    )
    assert qa["engine"] == "gcp_enterprise_rest"
    assert qa["answer"] == "Enterprise REST answer"

    deleted = await notebook_delete(
        notebook_ids=["nb-gcp-1"],
        workspace_dir=str(tmp_path),
    )
    assert deleted["engine"] == "gcp_enterprise_rest"
