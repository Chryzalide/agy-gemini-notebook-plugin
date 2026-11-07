"""Official Google Cloud Discovery Engine v1alpha Notebooks REST Client.

Implements the official NotebookLM Enterprise API documented at:
- https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks
- https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks-sources
- https://docs.cloud.google.com/gemini/enterprise/docs/reference/rest/v1alpha/projects.locations.notebooks
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from agy_gemini_notebook.config import (
    PluginConfig,
    extract_and_validate_resource_id,
    validate_gcp_endpoint_location,
    validate_gcp_location,
    validate_gcp_project_number,
    validate_workspace_file_path,
)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB safety limit for in-memory upload


def _safe_segment(raw_value: str, field_name: str = "resource_id") -> str:
    """Validate a REST URL path segment and return its URL-encoded form."""
    cleaned = extract_and_validate_resource_id(raw_value, field_name)
    return quote(cleaned, safe="")

# Supported MIME types for official `sources:uploadFile` endpoint
EXT_MIME_OVERRIDES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class GcpNotebookEnterpriseClient:
    """Async client for the official Google Cloud Discovery Engine v1alpha Notebooks API."""

    def __init__(
        self,
        config: PluginConfig | None = None,
        project_number: str | None = None,
        location: str | None = None,
        endpoint_location: str | None = None,
        engine_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or PluginConfig()
        raw_project = project_number or self.config.gcp_project_number
        self.project_number = validate_gcp_project_number(raw_project)
        self.location = validate_gcp_location(location or self.config.gcp_location)
        self.endpoint_location = validate_gcp_endpoint_location(
            endpoint_location or self.config.gcp_endpoint_location
        )
        raw_engine = engine_id or self.config.gcp_engine_id
        self.engine_id = (
            extract_and_validate_resource_id(raw_engine, "gcp_engine_id")
            if raw_engine and raw_engine.strip()
            else None
        )
        self._transport = transport

    def _client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    def _require_project(self) -> str:
        if not self.project_number:
            raise ValueError(
                "GCP_PROJECT_NUMBER is required for the official Google Cloud Notebooks API. "
                "Set the GCP_PROJECT_NUMBER environment variable or configure it in .agents/notebook.json."
            )
        return _safe_segment(self.project_number, "gcp_project_number")

    @property
    def base_url(self) -> str:
        project = self._require_project()
        loc = quote(validate_gcp_location(self.location), safe="")
        ep_loc = validate_gcp_endpoint_location(self.endpoint_location)
        return (
            f"https://{ep_loc}-discoveryengine.googleapis.com"
            f"/v1alpha/projects/{project}/locations/{loc}"
        )

    @property
    def upload_base_url(self) -> str:
        project = self._require_project()
        loc = quote(validate_gcp_location(self.location), safe="")
        ep_loc = validate_gcp_endpoint_location(self.endpoint_location)
        return (
            f"https://{ep_loc}-discoveryengine.googleapis.com"
            f"/upload/v1alpha/projects/{project}/locations/{loc}"
        )

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        token = self.config.get_gcloud_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    # -------------------------------------------------------------------------
    # Notebooks CRUD & Sharing (`projects.locations.notebooks`)
    # -------------------------------------------------------------------------

    async def create_notebook(self, title: str) -> dict[str, Any]:
        """Create a new Gemini Notebook (`POST .../notebooks`)."""
        url = f"{self.base_url}/notebooks"
        async with self._client(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json={"title": title})
            resp.raise_for_status()
            return resp.json()

    async def get_notebook(self, notebook_id: str) -> dict[str, Any]:
        """Get a Gemini Notebook and its sources (`GET .../notebooks/{notebookId}`)."""
        safe_nb = _safe_segment(notebook_id, "notebook_id")
        url = f"{self.base_url}/notebooks/{safe_nb}"
        async with self._client(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def list_recently_viewed(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List recently viewed notebooks (`GET .../notebooks:listRecentlyViewed`)."""
        url = f"{self.base_url}/notebooks:listRecentlyViewed"
        async with self._client(timeout=30.0) as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                params={"pageSize": page_size},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("notebooks", [])

    async def batch_delete_notebooks(self, notebook_ids: list[str]) -> dict[str, Any]:
        """Delete one or more notebooks (`POST .../notebooks:batchDelete`)."""
        project = self._require_project()
        loc = quote(validate_gcp_location(self.location), safe="")
        names = [
            f"projects/{project}/locations/{loc}/notebooks/{_safe_segment(n, 'notebook_id')}"
            for n in notebook_ids
        ]
        url = f"{self.base_url}/notebooks:batchDelete"
        async with self._client(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json={"names": names})
            resp.raise_for_status()
            return resp.json()

    async def share_notebook(
        self,
        notebook_id: str,
        email: str,
        role: str = "PROJECT_ROLE_READER",
        notify_via_email: bool = True,
    ) -> dict[str, Any]:
        """Share a notebook (`POST .../notebooks/{notebookId}:share`).

        Valid roles:
        - PROJECT_ROLE_OWNER
        - PROJECT_ROLE_WRITER
        - PROJECT_ROLE_READER
        - PROJECT_ROLE_NOT_SHARED (revokes access)
        """
        safe_nb = _safe_segment(notebook_id, "notebook_id")
        url = f"{self.base_url}/notebooks/{safe_nb}:share"
        payload = {
            "accountAndRoles": [{"email": email, "role": role}],
            "notifyViaEmail": notify_via_email,
        }
        async with self._client(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            return resp.json()

    # -------------------------------------------------------------------------
    # Sources Ingestion & Management (`projects.locations.notebooks.sources`)
    # -------------------------------------------------------------------------

    async def batch_create_sources(
        self,
        notebook_id: str,
        user_contents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add text, web, YouTube, or Google Drive sources (`POST .../sources:batchCreate`).

        Example `user_contents` items:
        - `{"textContent": {"sourceName": "Notes", "content": "..."}}`
        - `{"webContent": {"url": "https://...", "sourceName": "Doc"}}`
        - `{"videoContent": {"youtubeUrl": "https://www.youtube.com/watch?v=..."}}`
        - `{"googleDriveContent": {"documentId": "...", "mimeType": "application/vnd.google-apps.document", "sourceName": "Spec"}}`
        """
        safe_nb = _safe_segment(notebook_id, "notebook_id")
        url = f"{self.base_url}/notebooks/{safe_nb}/sources:batchCreate"
        async with self._client(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                json={"userContents": user_contents},
            )
            resp.raise_for_status()
            return resp.json()

    async def upload_file_source(
        self,
        notebook_id: str,
        file_path: str | Path,
        display_name: str | None = None,
        workspace_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Upload a local file to a notebook (`POST /upload/v1alpha/.../sources:uploadFile`)."""
        safe_nb = _safe_segment(notebook_id, "notebook_id")
        path = validate_workspace_file_path(file_path, workspace_dir=workspace_dir)
        file_size = path.stat().st_size
        if file_size > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"File {path.name!r} ({file_size} bytes) exceeds maximum upload limit ({MAX_UPLOAD_BYTES} bytes)."
            )

        ext = path.suffix.lower()
        content_type = (
            EXT_MIME_OVERRIDES.get(ext)
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        file_name = (display_name or path.name).replace("\r", "").replace("\n", "")
        raw_bytes = path.read_bytes()

        url = f"{self.upload_base_url}/notebooks/{safe_nb}/sources:uploadFile"
        headers = self._headers(
            {
                "X-Goog-Upload-File-Name": file_name,
                "X-Goog-Upload-Protocol": "raw",
                "Content-Type": content_type,
            }
        )
        async with self._client(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, content=raw_bytes)
            resp.raise_for_status()
            return resp.json()

    async def get_source(self, notebook_id: str, source_id: str) -> dict[str, Any]:
        """Get metadata and ingestion status of a source (`GET .../sources/{sourceId}`)."""
        safe_nb = _safe_segment(notebook_id, "notebook_id")
        safe_src = _safe_segment(source_id, "source_id")
        url = f"{self.base_url}/notebooks/{safe_nb}/sources/{safe_src}"
        async with self._client(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def batch_delete_sources(
        self,
        notebook_id: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        """Delete one or more sources from a notebook (`POST .../sources:batchDelete`)."""
        project = self._require_project()
        loc = quote(validate_gcp_location(self.location), safe="")
        safe_nb = _safe_segment(notebook_id, "notebook_id")
        names = [
            f"projects/{project}/locations/{loc}/notebooks/{safe_nb}/sources/{_safe_segment(s, 'source_id')}"
            for s in source_ids
        ]
        url = f"{self.base_url}/notebooks/{safe_nb}/sources:batchDelete"
        async with self._client(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json={"names": names})
            resp.raise_for_status()
            return resp.json()

    # -------------------------------------------------------------------------
    # Audio Overviews (`projects.locations.notebooks.audioOverviews`)
    # -------------------------------------------------------------------------

    async def create_audio_overview(
        self,
        notebook_id: str,
        episode_focus: str = "",
        language_code: str = "en",
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate an Audio Overview podcast for a notebook (`POST .../audioOverviews`)."""
        safe_nb = _safe_segment(notebook_id, "notebook_id")
        url = f"{self.base_url}/notebooks/{safe_nb}/audioOverviews"
        generation_options: dict[str, Any] = {
            "languageCode": language_code,
        }
        if episode_focus:
            generation_options["episodeFocus"] = episode_focus
        if source_ids:
            generation_options["sourceIds"] = [
                {"id": extract_and_validate_resource_id(sid, "source_id")} for sid in source_ids
            ]

        async with self._client(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                json={"generationOptions": generation_options},
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_audio_overview(
        self,
        notebook_id: str,
        audio_overview_id: str = "default",
    ) -> dict[str, Any]:
        """Delete an Audio Overview (`DELETE .../audioOverviews/{audioOverviewId}`)."""
        safe_nb = _safe_segment(notebook_id, "notebook_id")
        safe_audio = _safe_segment(audio_overview_id, "audio_overview_id")
        url = f"{self.base_url}/notebooks/{safe_nb}/audioOverviews/{safe_audio}"
        async with self._client(timeout=30.0) as client:
            resp = await client.delete(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    # -------------------------------------------------------------------------
    # Optional Enterprise Grounded Q&A (`servingConfigs:answer`)
    # -------------------------------------------------------------------------

    async def query_enterprise(
        self,
        question: str,
        engine_id: str | None = None,
        serving_config_id: str = "default_search",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a grounded Enterprise Q&A call via Discovery Engine `servingConfigs:answer`.

        Used when running in a pure Google Cloud Enterprise environment (`GCP_ENGINE_ID` set)
        without browser cookies.
        """
        target_engine = engine_id or self.engine_id
        if not target_engine:
            raise ValueError(
                "GCP_ENGINE_ID is required for pure REST Discovery Engine `servingConfigs:answer` queries."
            )
        safe_engine = _safe_segment(target_engine, "engine_id")
        safe_serving = _safe_segment(serving_config_id, "serving_config_id")
        url = (
            f"{self.base_url}/collections/default_collection"
            f"/engines/{safe_engine}/servingConfigs/{safe_serving}:answer"
        )
        payload: dict[str, Any] = {
            "query": {"text": question},
            "answerGenerationSpec": {
                "includeCitations": True,
                "ignoreAdversarialQuery": True,
                "ignoreNonAnswerSeekingQuery": False,
            },
        }
        if session_id:
            payload["session"] = session_id

        async with self._client(timeout=60.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()

        answer_obj = data.get("answer", {})
        answer_text = answer_obj.get("answerText", "")
        citations = answer_obj.get("citations", [])
        references = answer_obj.get("references", [])
        return {
            "status": "ok",
            "engine": "gcp_enterprise_rest",
            "engine_id": target_engine,
            "question": question,
            "answer": answer_text,
            "citations": citations,
            "references": references,
            "raw": data,
        }
