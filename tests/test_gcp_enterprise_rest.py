"""Unit tests for workspace notebook bindings and GCP v1alpha REST URL construction."""

from pathlib import Path

from agy_gemini_notebook.config import (
    PluginConfig,
    WorkspaceNotebookBinding,
    load_workspace_binding,
    save_workspace_binding,
)
from agy_gemini_notebook.engines.gcp_enterprise_rest import GcpNotebookEnterpriseClient


def test_workspace_binding_roundtrip(tmp_path: Path) -> None:
    binding = WorkspaceNotebookBinding(
        notebook_id="nb-12345",
        title="Architecture Specs",
        active_source_ids=["src-1", "src-2"],
        gcp_project_number="99887766",
    )
    saved = save_workspace_binding(binding, workspace_dir=tmp_path)
    assert saved.is_file()

    loaded = load_workspace_binding(workspace_dir=tmp_path)
    assert loaded is not None
    assert loaded.notebook_id == "nb-12345"
    assert loaded.title == "Architecture Specs"
    assert loaded.active_source_ids == ["src-1", "src-2"]
    assert loaded.gcp_project_number == "99887766"


def test_gcp_client_urls() -> None:
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
