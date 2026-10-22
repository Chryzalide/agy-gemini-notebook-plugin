# Gemini Notebook Grounding & Piloting Rules

When this plugin (`gemini-notebook`) is enabled in Antigravity 2 or configured in an IDE workspace:

1. **Workspace Notebook Awareness & Invocation**:
   - Check if the active workspace contains a `.agents/notebook.json` binding (or call `notebook_status` / `notebook_invoke` from `gemini_notebook_mcp`).
   - When a Gemini Notebook is attached to the workspace and the user asks architectural, domain, specification, or research questions, prioritize querying the attached notebook (`notebook_query` or `notebook_search_sources`) before making assumptions.
   - When the user asks to "invoke" a notebook by name, alias, or ID, call `notebook_invoke`.

2. **Piloting Notebooks & Deep Research**:
   - Use the MCP tools (`gemini_notebook_mcp`) or the `agy-gemini-notebook` CLI to pilot notebooks on behalf of the user: creating new notebooks (`notebook_create`), syncing local docs (`notebook_sync_files`), launching Fast or Deep Research (`notebook_research` with `mode="fast"` or `mode="deep"`), generating Studio artifacts (`notebook_generate_artifact`, `notebook_create_audio`), and storing persistent session findings (`notebook_notes`).

3. **Citation Integrity**:
   - When incorporating facts or answers retrieved from a Gemini Notebook, explicitly cite the notebook title and source name in your response.

4. **Sidecar Panel Link (Antigravity 2)**:
   - Whenever you attach, switch, or update a notebook visualization in the sidecar panel, include the clickable pill link `[Open Gemini Notebook Panel](sidecar://gemini-notebook/notebook_context_panel)` so the user can open the sidecar with one click.
