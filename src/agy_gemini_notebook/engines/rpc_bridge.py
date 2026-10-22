"""RPC Bridge Engine for Conversational Q&A (`notebook_query`) and Consumer Accounts.

Why this engine exists alongside `gcp_enterprise_rest.py`:
- The official Google Cloud Discovery Engine `v1alpha/projects.locations.notebooks` REST API
  handles Notebook CRUD, sharing, source ingestion (`batchCreate`, `uploadFile`), and
  `audioOverviews`, but does NOT yet expose a `:query` or `:converse` endpoint on notebooks,
  nor does `sources.get` return full source text.
- Furthermore, personal Google accounts (`notebooklm.google.com`) do not have a GCP project.
- This bridge wraps `notebooklm-py` (when installed via `uv sync --extra rpc`) to provide
  grounded conversational Q&A (`query_notebook`) and full-text source extraction.
"""

from __future__ import annotations

from typing import Any


class NotebookRpcBridge:
    """Adapter around `notebooklm-py` for grounded RAG queries and full-text extraction."""

    @staticmethod
    def is_available() -> bool:
        """Check whether the optional `notebooklm-py` package is installed."""
        try:
            import notebooklm  # noqa: F401

            return True
        except ImportError:
            return False

    async def list_notebooks(self) -> list[dict[str, Any]]:
        """List notebooks via the RPC bridge (`notebooklm-py`)."""
        if not self.is_available():
            raise RuntimeError(
                "Optional dependency `notebooklm-py` is not installed. "
                "Install it with `uv sync --extra rpc` and run `uv run notebooklm login`."
            )
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            notebooks = await client.notebooks.list()
            return [
                {
                    "notebookId": getattr(nb, "id", str(nb)),
                    "title": getattr(nb, "title", ""),
                }
                for nb in notebooks
            ]

    async def query_notebook(
        self,
        notebook_id: str,
        question: str,
        source_ids: list[str] | None = None,
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
                    "the RPC bridge (`uv sync --extra rpc` + `uv run notebooklm login`)."
                ),
            }

        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            kwargs: dict[str, Any] = {}
            if source_ids:
                kwargs["source_ids"] = source_ids
            result = await client.chat.ask(notebook_id, question, **kwargs)
            return {
                "status": "ok",
                "notebook_id": notebook_id,
                "question": question,
                "answer": getattr(result, "answer", str(result)),
                "citations": getattr(result, "citations", []),
            }

    async def get_source_fulltext(
        self,
        notebook_id: str,
        source_id: str,
    ) -> dict[str, Any]:
        """Retrieve the full indexed text of a source inside a notebook via RPC."""
        if not self.is_available():
            raise RuntimeError(
                "Full-text source extraction requires `notebooklm-py` (`uv sync --extra rpc`) "
                "because the official v1alpha `sources.get` endpoint only returns metadata "
                "(wordCount, tokenCount, status)."
            )

        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            source = await client.sources.get_fulltext(notebook_id, source_id)
            return {
                "notebook_id": notebook_id,
                "source_id": source_id,
                "content": getattr(source, "content", str(source)),
            }
