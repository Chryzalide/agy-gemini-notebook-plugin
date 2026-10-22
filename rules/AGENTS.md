# Gemini Notebook Grounding Rules

When this plugin (`gemini-notebook`) is enabled in Antigravity 2:

1. **Workspace Notebook Awareness**:
   - Check if the active workspace contains a `.agents/notebook.json` binding (or call `notebook_status` from `gemini_notebook_mcp`).
   - When a Gemini Notebook is attached to the workspace and the user asks architectural, domain, specification, or research questions, prioritize querying the attached notebook (`notebook_query`) or inspecting its sources (`notebook_get`) before making assumptions.

2. **Citation Integrity**:
   - When incorporating facts or answers retrieved from a Gemini Notebook, explicitly cite the notebook title and source name in your response.

3. **Sidecar Panel Link**:
   - Whenever you attach, switch, or update a notebook visualization in the sidecar panel, include the clickable pill link `[Open Gemini Notebook Panel](sidecar://gemini-notebook/notebook_context_panel)` so the user can open the sidecar with one click.
