"""Configuration and Workspace Binding Management (.agents/notebook.json)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

ALLOWED_GCP_ENDPOINT_LOCATIONS: frozenset[str] = frozenset({"us", "eu", "global"})
_SAFE_LOCATION_RE = re.compile(r"^[a-z0-9-]+$")
_SAFE_RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_GCP_RESOURCE_NAME_RE = re.compile(
    r"^projects/[A-Za-z0-9._-]+/locations/[a-z0-9-]+/notebooks/([A-Za-z0-9._-]+)(?:/sources/([A-Za-z0-9._-]+))?$"
)
_BLOCKED_SYNC_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".git",
        ".ssh",
        ".aws",
        ".gnupg",
        ".notebooklm",
        ".notebooklm-auth",
        "storage_state.json",
        "server_info.json",
        "id_rsa",
        "id_ed25519",
        "credentials.db",
    }
)


def extract_and_validate_resource_id(raw_id: str, field_name: str = "resource_id") -> str:
    """Extract a bare resource ID (stripping canonical `projects/.../notebooks/` if present) and validate characters."""
    cleaned = (raw_id or "").strip()
    if "/" in cleaned:
        m = _GCP_RESOURCE_NAME_RE.match(cleaned)
        if not m:
            raise ValueError(
                f"Invalid {field_name} {raw_id!r}: resource paths must match canonical 'projects/<project>/locations/<loc>/notebooks/<id>'."
            )
        cleaned = m.group(2) or m.group(1)

    if (
        not cleaned
        or ".." in cleaned
        or cleaned == "."
        or not _SAFE_RESOURCE_ID_RE.match(cleaned)
    ):
        raise ValueError(
            f"Invalid {field_name} {raw_id!r}: must contain only alphanumeric characters, dots, underscores, or hyphens."
        )
    return cleaned


def validate_gcp_endpoint_location(value: str) -> str:
    """Ensure `gcp_endpoint_location` is strictly one of the official Google Cloud multi-regions."""
    cleaned = (value or "").strip().lower()
    if cleaned not in ALLOWED_GCP_ENDPOINT_LOCATIONS:
        raise ValueError(
            f"Invalid gcp_endpoint_location {value!r}. Expected one of: {sorted(ALLOWED_GCP_ENDPOINT_LOCATIONS)}."
        )
    return cleaned


def validate_gcp_location(value: str) -> str:
    """Ensure `gcp_location` contains only lowercase alphanumeric characters and hyphens."""
    cleaned = (value or "").strip().lower()
    if not cleaned or not _SAFE_LOCATION_RE.match(cleaned):
        raise ValueError(
            f"Invalid gcp_location {value!r}. Expected a valid region identifier (e.g. 'global', 'us', 'eu')."
        )
    return cleaned


def validate_gcp_project_number(value: str | None) -> str | None:
    """Ensure `gcp_project_number` (if set) is a safe Google Cloud project number or ID."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned in (".", "..") or not _SAFE_RESOURCE_ID_RE.match(cleaned):
        raise ValueError(f"Invalid gcp_project_number {value!r}.")
    return cleaned


def validate_workspace_file_path(
    file_path: str | Path,
    workspace_dir: str | Path | None = None,
) -> Path:
    """Resolve `file_path` and enforce workspace containment and sensitive-file exclusions when `workspace_dir` is given."""
    raw_path = Path(file_path).expanduser()
    ws_root = resolve_workspace_dir(workspace_dir) if workspace_dir is not None else None

    if ws_root is not None and not raw_path.is_absolute():
        resolved = (ws_root / raw_path).resolve()
    else:
        resolved = raw_path.resolve()

    if ws_root is not None and not resolved.is_relative_to(ws_root):
        raise ValueError(
            f"File path {str(file_path)!r} resolves outside the active workspace directory ({ws_root})."
        )

    check_parts = (
        resolved.relative_to(ws_root).parts if (ws_root is not None and resolved.is_relative_to(ws_root)) else resolved.parts
    )
    for part in check_parts:
        lower_part = part.lower()
        if lower_part in _BLOCKED_SYNC_NAMES or lower_part.startswith(".env.") or lower_part.endswith(".cookies.json"):
            raise ValueError(f"Uploading sensitive credential or configuration file {part!r} is not permitted.")

    if not resolved.is_file():
        raise FileNotFoundError(f"File not found: {resolved}")
    return resolved


class WorkspaceNotebookBinding(BaseModel):
    """Represents the Gemini Notebook attached to an Antigravity 2 or VS Code workspace."""

    notebook_id: str = Field(..., description="Unique ID of the primary attached Gemini Notebook")
    title: str | None = Field(default=None, description="Human-readable notebook title")
    engine: str = Field(
        default="auto",
        description="Preferred engine: 'gcp_rest', 'rpc_bridge', or 'auto'",
    )
    active_source_ids: list[str] = Field(
        default_factory=list,
        description="Optional filter of active source IDs within the notebook (empty = all)",
    )
    aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Optional mapping of short alias names to notebook IDs for multi-notebook workspaces",
    )
    gcp_project_number: str | None = Field(default=None)
    gcp_location: str = Field(default="global")
    gcp_endpoint_location: str = Field(default="global")
    gcp_engine_id: str | None = Field(
        default=None,
        description="Optional Discovery Engine app/engine ID for Enterprise Grounded Q&A",
    )

    @field_validator("notebook_id")
    @classmethod
    def _check_notebook_id(cls, v: str) -> str:
        return extract_and_validate_resource_id(v, "notebook_id")

    @field_validator("active_source_ids")
    @classmethod
    def _check_active_source_ids(cls, v: list[str]) -> list[str]:
        return [extract_and_validate_resource_id(sid, "source_id") for sid in v if str(sid).strip()]

    @field_validator("aliases")
    @classmethod
    def _check_aliases(cls, v: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for k, target in v.items():
            key = extract_and_validate_resource_id(k, "alias")
            cleaned[key] = extract_and_validate_resource_id(target, "alias_notebook_id")
        return cleaned

    @field_validator("gcp_project_number")
    @classmethod
    def _check_project_number(cls, v: str | None) -> str | None:
        return validate_gcp_project_number(v)

    @field_validator("gcp_location")
    @classmethod
    def _check_location(cls, v: str) -> str:
        return validate_gcp_location(v)

    @field_validator("gcp_endpoint_location")
    @classmethod
    def _check_endpoint_location(cls, v: str) -> str:
        return validate_gcp_endpoint_location(v)

    @field_validator("gcp_engine_id")
    @classmethod
    def _check_engine_id(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        return extract_and_validate_resource_id(v, "gcp_engine_id")


class PluginConfig(BaseModel):
    """Global configuration resolved from environment variables and workspace files."""

    gcp_project_number: str | None = Field(
        default_factory=lambda: os.environ.get("GCP_PROJECT_NUMBER")
    )
    gcp_location: str = Field(
        default_factory=lambda: os.environ.get("GCP_LOCATION", "global")
    )
    gcp_endpoint_location: str = Field(
        default_factory=lambda: os.environ.get("GCP_ENDPOINT_LOCATION", "global")
    )
    gcp_access_token: str | None = Field(
        default_factory=lambda: os.environ.get("GCP_ACCESS_TOKEN")
    )
    gcp_engine_id: str | None = Field(
        default_factory=lambda: (
            os.environ.get("GCP_ENGINE_ID") or os.environ.get("GCP_DATA_STORE_ID")
        )
    )
    notebooklm_profile: str | None = Field(
        default_factory=lambda: os.environ.get("NOTEBOOKLM_PROFILE")
    )
    notebooklm_storage_path: str | None = Field(
        default_factory=lambda: os.environ.get("NOTEBOOKLM_STORAGE_PATH")
    )

    def get_gcloud_token(self) -> str:
        """Retrieve a valid OAuth2 Bearer token from env or `gcloud auth print-access-token`."""
        if self.gcp_access_token:
            return self.gcp_access_token.strip()
        try:
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return result.stdout.strip()
        except Exception as exc:
            raise RuntimeError(
                "Unable to obtain Google Cloud access token. "
                "Run `gcloud auth login` or set GCP_ACCESS_TOKEN."
            ) from exc


def resolve_workspace_dir(workspace_dir: str | Path | None = None) -> Path:
    """Resolve the active workspace directory across Antigravity 2, VS Code, or CLI."""
    if workspace_dir:
        return Path(workspace_dir).expanduser().resolve()
    env_ws = os.environ.get("AGY_WORKSPACE_DIR") or os.environ.get("VSCODE_WORKSPACE_FOLDER")
    if env_ws:
        return Path(env_ws).expanduser().resolve()

    cwd = Path.cwd().resolve()
    home = Path.home().resolve()
    for candidate in (cwd, *cwd.parents):
        if candidate != cwd and candidate in (home, Path("/")):
            break
        if (candidate / ".agents" / "notebook.json").is_file():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return cwd


def get_binding_path(workspace_dir: str | Path | None = None) -> Path:
    """Return the path to `.agents/notebook.json` inside the target workspace."""
    base = Path(workspace_dir).expanduser().resolve() if workspace_dir else resolve_workspace_dir()
    if base == Path("/"):
        raise ValueError("Refusing to use filesystem root '/' as workspace directory.")
    return base / ".agents" / "notebook.json"


def load_workspace_binding(
    workspace_dir: str | Path | None = None,
) -> WorkspaceNotebookBinding | None:
    """Load the attached notebook configuration for the workspace, if present."""
    path = get_binding_path(workspace_dir)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return WorkspaceNotebookBinding.model_validate(data)


def save_workspace_binding(
    binding: WorkspaceNotebookBinding,
    workspace_dir: str | Path | None = None,
) -> Path:
    """Save the attached notebook configuration to `.agents/notebook.json`."""
    path = get_binding_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(binding.model_dump(exclude_none=True), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def describe_environment(workspace_dir: str | Path | None = None) -> dict[str, Any]:
    """Return diagnostic status of engines and current workspace notebook binding."""
    cfg = PluginConfig()
    binding = load_workspace_binding(workspace_dir)
    has_gcloud_token = False
    try:
        has_gcloud_token = bool(cfg.get_gcloud_token())
    except RuntimeError:
        has_gcloud_token = False

    project_num = cfg.gcp_project_number or (binding.gcp_project_number if binding else None)
    engine_id = cfg.gcp_engine_id or (binding.gcp_engine_id if binding else None)

    return {
        "workspace_dir": str(Path(workspace_dir) if workspace_dir else resolve_workspace_dir()),
        "binding_file": str(get_binding_path(workspace_dir)),
        "attached_notebook": binding.model_dump() if binding else None,
        "gcp_enterprise_rest": {
            "configured": bool(project_num),
            "project_number": project_num,
            "location": binding.gcp_location if binding and binding.gcp_location else cfg.gcp_location,
            "endpoint_location": (
                binding.gcp_endpoint_location
                if binding and binding.gcp_endpoint_location
                else cfg.gcp_endpoint_location
            ),
            "engine_id": engine_id,
            "gcloud_authenticated": has_gcloud_token,
        },
    }
