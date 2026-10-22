"""Command-Line Interface (`agy-gemini-notebook`) for piloting Gemini Notebooks from any IDE or harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from agy_gemini_notebook.mcp_server import (
    main as mcp_main,
)
from agy_gemini_notebook.mcp_server import (
    notebook_add_source,
    notebook_attach,
    notebook_create,
    notebook_create_audio,
    notebook_generate_artifact,
    notebook_get,
    notebook_invoke,
    notebook_list,
    notebook_notes,
    notebook_query,
    notebook_research,
    notebook_research_status,
    notebook_search_sources,
    notebook_status,
    notebook_sync_files,
)


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


async def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    cmd = args.command
    if cmd == "status":
        return await notebook_status(workspace_dir=args.workspace)
    if cmd == "list":
        return await notebook_list(
            page_size=args.limit,
            query=args.query,
            engine=args.engine,
            workspace_dir=args.workspace,
        )
    if cmd == "get":
        return await notebook_get(
            notebook_id=args.notebook_id,
            engine=args.engine,
            workspace_dir=args.workspace,
        )
    if cmd == "attach":
        return await notebook_attach(
            notebook_id=args.notebook_id,
            title=args.title,
            alias=args.alias,
            engine=args.engine,
            workspace_dir=args.workspace,
        )
    if cmd == "invoke":
        return await notebook_invoke(
            target=args.target,
            question=args.question,
            include_sources=not args.no_sources,
            engine=args.engine,
            workspace_dir=args.workspace,
        )
    if cmd == "create":
        return await notebook_create(
            title=args.title,
            attach_to_workspace=not args.no_attach,
            alias=args.alias,
            emoji=args.emoji,
            engine=args.engine,
            workspace_dir=args.workspace,
        )
    if cmd == "query":
        return await notebook_query(
            question=args.question,
            notebook_id=args.notebook_id,
            conversation_id=args.conversation_id,
            save_as_note=args.save_as_note,
            note_title=args.note_title,
            engine=args.engine,
            workspace_dir=args.workspace,
        )
    if cmd == "search":
        return await notebook_search_sources(
            query=args.query,
            notebook_id=args.notebook_id,
            limit=args.limit,
            workspace_dir=args.workspace,
        )
    if cmd == "sync":
        return await notebook_sync_files(
            file_paths=args.files,
            notebook_id=args.notebook_id,
            wait=args.wait,
            engine=args.engine,
            workspace_dir=args.workspace,
        )
    if cmd == "add-source":
        return await notebook_add_source(
            source_type=args.type,
            value=args.value,
            title=args.title,
            notebook_id=args.notebook_id,
            wait=args.wait,
            engine=args.engine,
            workspace_dir=args.workspace,
        )
    if cmd == "research":
        return await notebook_research(
            query=args.query,
            mode=args.mode,
            source=args.source,
            wait=not args.no_wait,
            import_sources=not args.no_import,
            cited_only=args.cited_only,
            timeout=args.timeout,
            notebook_id=args.notebook_id,
            create_notebook_title=args.new_notebook,
            workspace_dir=args.workspace,
        )
    if cmd == "research-status":
        return await notebook_research_status(
            task_id=args.task_id,
            wait=args.wait,
            import_sources=args.import_sources,
            cited_only=args.cited_only,
            timeout=args.timeout,
            cancel=args.cancel,
            notebook_id=args.notebook_id,
            workspace_dir=args.workspace,
        )
    if cmd == "audio":
        return await notebook_create_audio(
            episode_focus=args.focus,
            language_code=args.language,
            audio_format=args.format,
            audio_length=args.length,
            wait=args.wait,
            notebook_id=args.notebook_id,
            engine=args.engine,
            workspace_dir=args.workspace,
        )
    if cmd == "generate":
        return await notebook_generate_artifact(
            artifact_type=args.artifact_type,
            instructions=args.instructions,
            report_format=args.report_format,
            language_code=args.language,
            wait=args.wait,
            timeout=args.timeout,
            notebook_id=args.notebook_id,
            workspace_dir=args.workspace,
        )
    if cmd == "notes":
        return await notebook_notes(
            action=args.action,
            title=args.title,
            content=args.content,
            note_id=args.note_id,
            notebook_id=args.notebook_id,
            workspace_dir=args.workspace,
        )
    raise ValueError(f"Unknown command: {cmd}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agy-gemini-notebook",
        description=(
            "Invoke and pilot Gemini Notebooks (NotebookLM & NotebookLM Enterprise) "
            "from Antigravity 2, VS Code, Cursor, Claude Code, or terminal scripts."
        ),
    )
    parser.add_argument(
        "-w",
        "--workspace",
        default=None,
        help="Workspace directory containing `.agents/notebook.json`.",
    )
    parser.add_argument(
        "-e",
        "--engine",
        default="auto",
        choices=["auto", "gcp_rest", "rpc_bridge"],
        help="Backend engine preference (default: auto).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser("status", help="Show workspace notebook binding and engine auth status.")

    # list
    p_list = sub.add_parser("list", help="List available Gemini Notebooks.")
    p_list.add_argument("-l", "--limit", type=int, default=25, help="Max notebooks to return.")
    p_list.add_argument("-q", "--query", default=None, help="Filter notebooks by title substring.")

    # get
    p_get = sub.add_parser("get", help="Get metadata, summary, and sources for a notebook.")
    p_get.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")

    # attach
    p_attach = sub.add_parser("attach", help="Attach a notebook to the workspace.")
    p_attach.add_argument("notebook_id", help="Notebook ID to attach.")
    p_attach.add_argument("-t", "--title", default=None, help="Display title.")
    p_attach.add_argument("-a", "--alias", default=None, help="Optional workspace alias.")

    # invoke
    p_invoke = sub.add_parser(
        "invoke",
        help="Invoke a notebook by ID, alias, or title (optionally asking a grounded question).",
    )
    p_invoke.add_argument(
        "-t",
        "--target",
        default=None,
        help="Notebook ID, workspace alias, or notebook title (defaults to active notebook).",
    )
    p_invoke.add_argument("-q", "--question", default=None, help="Grounded question to ask.")
    p_invoke.add_argument(
        "--no-sources",
        action="store_true",
        help="Skip fetching notebook metadata/sources when asking a question.",
    )

    # create
    p_create = sub.add_parser("create", help="Create a new Gemini Notebook.")
    p_create.add_argument("title", help="Title for the new notebook.")
    p_create.add_argument("-a", "--alias", default=None, help="Optional workspace alias.")
    p_create.add_argument("--emoji", default=None, help="Optional display emoji.")
    p_create.add_argument(
        "--no-attach",
        action="store_true",
        help="Do not attach the newly created notebook to `.agents/notebook.json`.",
    )

    # query
    p_query = sub.add_parser("query", help="Ask a grounded question against a notebook.")
    p_query.add_argument("question", help="Question to ask.")
    p_query.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")
    p_query.add_argument("-c", "--conversation-id", default=None, help="Follow-up conversation ID.")
    p_query.add_argument("--save-as-note", action="store_true", help="Save answer as a note.")
    p_query.add_argument("--note-title", default=None, help="Title when saving as a note.")

    # search
    p_search = sub.add_parser("search", help="Search indexed passages across notebook sources.")
    p_search.add_argument("query", help="Search query.")
    p_search.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")
    p_search.add_argument("-l", "--limit", type=int, default=10, help="Max passage matches.")

    # sync
    p_sync = sub.add_parser("sync", help="Upload local workspace files into a notebook.")
    p_sync.add_argument("files", nargs="+", help="Local file paths to upload.")
    p_sync.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")
    p_sync.add_argument("--wait", action="store_true", help="Wait for indexing to complete.")

    # add-source
    p_add = sub.add_parser("add-source", help="Add text, web URL, YouTube, or Google Drive source.")
    p_add.add_argument(
        "--type",
        required=True,
        choices=["text", "web", "youtube", "gdrive"],
        help="Source type.",
    )
    p_add.add_argument("value", help="Content, URL, or Google Drive document ID.")
    p_add.add_argument("-t", "--title", default="Workspace Source", help="Source title.")
    p_add.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")
    p_add.add_argument("--wait", action="store_true", help="Wait for indexing to complete.")

    # research (Fast / Deep / Discover)
    p_res = sub.add_parser(
        "research",
        help="Launch Fast Research, Deep Research, or Web Source Discovery on a notebook.",
    )
    p_res.add_argument("query", help="Research topic or question.")
    p_res.add_argument(
        "-m",
        "--mode",
        default="fast",
        choices=["fast", "deep", "discover", "raw", "curious"],
        help="Research mode: 'fast', 'deep' (Deep Research), or 'discover'.",
    )
    p_res.add_argument(
        "-s",
        "--source",
        default="web",
        choices=["web", "drive"],
        help="Research source ('web' or 'drive').",
    )
    p_res.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")
    p_res.add_argument(
        "--new-notebook",
        default=None,
        help="Create a new notebook with this title before running research.",
    )
    p_res.add_argument("--no-wait", action="store_true", help="Start research without blocking.")
    p_res.add_argument(
        "--no-import",
        action="store_true",
        help="Do not auto-import discovered sources into the notebook.",
    )
    p_res.add_argument(
        "--cited-only",
        action="store_true",
        help="Import only sources cited in the Deep Research report.",
    )
    p_res.add_argument("--timeout", type=float, default=600.0, help="Wait timeout in seconds.")

    # research-status
    p_rstat = sub.add_parser("research-status", help="Poll, wait on, import, or cancel a research run.")
    p_rstat.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")
    p_rstat.add_argument("--task-id", default=None, help="Research task/run ID.")
    p_rstat.add_argument("--wait", action="store_true", help="Wait until completion.")
    p_rstat.add_argument(
        "--import-sources",
        action="store_true",
        help="Import discovered sources from the completed run.",
    )
    p_rstat.add_argument("--cited-only", action="store_true", help="Import only cited sources.")
    p_rstat.add_argument("--cancel", action="store_true", help="Cancel the research run.")
    p_rstat.add_argument("--timeout", type=float, default=600.0, help="Wait timeout in seconds.")

    # audio
    p_audio = sub.add_parser("audio", help="Generate an Audio Overview podcast.")
    p_audio.add_argument("-f", "--focus", default="", help="Episode focus / instructions.")
    p_audio.add_argument("-l", "--language", default="en", help="Language code (default: en).")
    p_audio.add_argument("--format", default=None, help="Audio format (deep-dive, brief, critique, debate).")
    p_audio.add_argument("--length", default=None, help="Audio length (short, default, long).")
    p_audio.add_argument("--wait", action="store_true", help="Wait for generation to finish.")
    p_audio.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")

    # generate
    p_gen = sub.add_parser("generate", help="Generate a Studio Artifact (report, mind_map, quiz, etc.).")
    p_gen.add_argument(
        "artifact_type",
        help="Artifact type: report, study_guide, briefing_doc, blog_post, mind_map, quiz, flashcards, slide_deck, data_table, infographic, video.",
    )
    p_gen.add_argument("-i", "--instructions", default="", help="Steering instructions or custom prompt.")
    p_gen.add_argument("--report-format", default="briefing_doc", help="Report format preset.")
    p_gen.add_argument("-l", "--language", default="en", help="Language code.")
    p_gen.add_argument("--wait", action="store_true", help="Wait for completion.")
    p_gen.add_argument("--timeout", type=float, default=300.0, help="Wait timeout in seconds.")
    p_gen.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")

    # notes
    p_notes = sub.add_parser("notes", help="Manage persistent notes in a notebook.")
    p_notes.add_argument(
        "action",
        choices=["list", "get", "create", "update", "delete"],
        help="Note operation.",
    )
    p_notes.add_argument("-t", "--title", default="Agent Note", help="Note title.")
    p_notes.add_argument("-c", "--content", default="", help="Note content.")
    p_notes.add_argument("--note-id", default=None, help="Note ID for get/update/delete.")
    p_notes.add_argument("-n", "--notebook-id", default=None, help="Notebook ID or alias.")

    # mcp
    p_mcp = sub.add_parser("mcp", help="Start the MCP server (`stdio`, `sse`, or `streamable-http`).")
    p_mcp.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport protocol.",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "mcp":
        mcp_main(["--transport", args.transport])
        return
    try:
        output = asyncio.run(_dispatch(args))
        _print_json(output)
    except Exception as exc:  # noqa: BLE001
        _print_json({"status": "error", "error": str(exc)})
        sys.exit(1)


if __name__ == "__main__":
    main()
