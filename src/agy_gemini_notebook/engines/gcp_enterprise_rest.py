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

import httpx

from agy_gemini_notebook.config import PluginConfig

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
    ) -> None:
        self.config = config or PluginConfig()
        self.project_number = project_number or self.config.gcp_project_number
        self.location = location or self.config.gcp_location
        self.endpoint_location = endpoint_location or self.config.gcp_endpoint_location

    def _require_project(self) -> str:
        if not self.project_number:
            raise ValueError(
                "GCP_PROJECT_NUMBER is required for the official Google Cloud Notebooks API. "
                "Set the GCP_PROJECT_NUMBER environment variable or configure it in .agents/notebook.json."
            )
        return self.project_number

    @property
    def base_url(self) -> str:
        project = self._require_project()
        return (
            f"https://{self.endpoint_location}-discoveryengine.googleapis.com"
            f"/v1alpha/projects/{project}/locations/{self.location}"
        )

    @property
    def upload_base_url(self) -> str:
        project = self._require_project()
        return (
            f"https://{self.endpoint_location}-discoveryengine.googleapis.com"
            f"/upload/v1alpha/projects/{project}/locations/{self.location}"
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
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json={"title": title})
            resp.raise_for_status()
            return resp.json()

    async def get_notebook(self, notebook_id: str) -> dict[str, Any]:
        """Get a Gemini Notebook and its sources (`GET .../notebooks/{notebookId}`)."""
        url = f"{self.base_url}/notebooks/{notebook_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def list_recently_viewed(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List recently viewed notebooks (`GET .../notebooks:listRecentlyViewed`)."""
        url = f"{self.base_url}/notebooks:listRecentlyViewed"
        async with httpx.AsyncClient(timeout=30.0) as client:
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
        names = [
            n if n.startswith("projects/")
            else f"projects/{project}/locations/{self.location}/notebooks/{n}"
            for n in notebook_ids
        ]
        url = f"{self.base_url}/notebooks:batchDelete"
        async with httpx.AsyncClient(timeout=30.0) as client:
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
        url = f"{self.base_url}/notebooks/{notebook_id}:share"
        payload = {
            "accountAndRoles": [{"email": email, "role": role}],
            "notifyViaEmail": notify_via_email,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
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
        url = f"{self.base_url}/notebooks/{notebook_id}/sources:batchCreate"
        async with httpx.AsyncClient(timeout=60.0) as client:
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
    ) -> dict[str, Any]:
        """Upload a local file to a notebook (`POST /upload/v1alpha/.../sources:uploadFile`)."""
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        ext = path.suffix.lower()
        content_type = (
            EXT_MIME_OVERRIDES.get(ext)
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        file_name = display_name or path.name
        raw_bytes = path.read_bytes()

        url = f"{self.upload_base_url}/notebooks/{notebook_id}/sources:uploadFile"
        headers = self._headers(
            {
                "X-Goog-Upload-File-Name": file_name,
                "X-Goog-Upload-Protocol": "raw",
                "Content-Type": content_type,
            }
        )
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, content=raw_bytes)
            resp.raise_for_status()
            return resp.json()

    async def get_source(self, notebook_id: str, source_id: str) -> dict[str, Any]:
        """Get metadata and ingestion status of a source (`GET .../sources/{sourceId}`)."""
        url = f"{self.base_url}/notebooks/{notebook_id}/sources/{source_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
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
        names = [
            s if s.startswith("projects/")
            else f"projects/{project}/locations/{self.location}/notebooks/{notebook_id}/sources/{s}"
            for s in source_ids
        ]
        url = f"{self.base_url}/notebooks/{notebook_id}/sources:batchDelete"
        async with httpx.AsyncClient(timeout=30.0) as client:
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
        url = f"{self.base_url}/notebooks/{notebook_id}/audioOverviews"
        generation_options: dict[str, Any] = {
            "languageCode": language_code,
        }
        if episode_focus:
            generation_options["episodeFocus"] = episode_focus
        if source_ids:
            generation_options["sourceIds"] = [{"id": sid} for sid in source_ids]

        async with httpx.AsyncClient(timeout=60.0) as client:
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
        url = f"{self.base_url}/notebooks/{notebook_id}/audioOverviews/{audio_overview_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()
