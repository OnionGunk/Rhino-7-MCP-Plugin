# Rhino 7 Live bridge

## Install from this repository

This is a local Windows MCP server. The verified host environment uses Python 3.13; code executed inside Rhino uses IronPython 2.7.

Clone or download this repository to a permanent folder, then run these commands there:

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Configure your MCP client to launch that environment's `python.exe`, with the absolute path to `server.py` as its argument. See `mcp.config.example.json` and replace both example paths. Reconnect the MCP client after configuration. The existing installed integration can remain in its current folder; this repository is a separate source copy.

Run the automated tests with:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests verify MCP discovery and the 14-tool interface without calling Rhino, and exercise the COM worker with mocks. Keep `sessions/`, virtual environments, logs, credentials and local configuration out of commits; `.gitignore` excludes these files. The Python tool runs with Rhino's current user permissions and should be connected only to a trusted MCP client.

A free local MCP connection for Windows Rhino 7 and Grasshopper 1. It uses Rhino's built-in, documented COM automation interface and IronPython 2.7. No Rhino 8 upgrade, Rhino plugin, network listener, or separate API key is required.

The installed copy lives in `.codex/integrations/rhino7` under your Windows user profile, with its own Python environment. Codex has a `rhino7` MCP server entry pointing to that copy.

## Use

1. Keep exactly one Rhino 7 instance open. Finish any active commands or dialogs.
2. Restart Codex once after initial setup to load the new connection.
3. Ask Codex to inspect Rhino and Grasshopper, then create or change geometry or a definition.

Available tools cover model status, object inspection, boxes, IronPython scripts, viewport capture, Grasshopper status, component search and creation, sliders, wiring, recomputing, new definitions, and saving to new `.gh` files.

Existing Grasshopper definitions are preserved when creating a new definition. The save tool refuses to overwrite an existing file. Modeling tool calls run sequentially on Rhino's own script thread. The Python-script tool supports broader Rhino and Grasshopper operations and should be used according to your current request.

## Verified on this computer

- Rhino **7.38.24338.17001**, Windows; Grasshopper **1.0.0007**.
- MCP initialization and discovery of 14 tools.
- Active Rhino and Grasshopper document inspection.
- Searching installed components, including names containing superscripts.
- A native five-node Grasshopper graph: two sliders, two Construct Domain nodes, and a Domain Box.
- Wiring, changing the height slider from 12 to 18, and recomputation without component errors.
- Saving the definition as `citycode-live-test.gh` and capturing the Rhino viewport.
- Unicode text and .NET floating-point results round-trip correctly.

This connection controls the desktop design environment. Connecting Grasshopper's city-design data to the Citycode website is a separate next step.

## Files

- `server.py`: standard MCP server using the open-source Python MCP SDK.
- `bridge_worker.py`: isolated COM worker that executes IronPython in Rhino 7.
- `requirements.txt`: pinned integration dependencies.

The MCP transport uses standard input/output. Rhino operations use its local COM interface. Commands do not pass through a public web service. Session files are temporary diagnostics and may remain briefly while Rhino holds a working-directory handle.

## References

- Rhino automation: https://developer.rhino3d.com/api/rhinoscript/introduction/external_access.htm
- Rhino RunScript: https://developer.rhino3d.com/api/rhinoscript/rhino_properties_methods/runscript.htm
- Codex MCP configuration: https://learn.chatgpt.com/docs/extend/mcp
- Community project inspected for architecture and Grasshopper API patterns: https://github.com/hov172/rhino_mcp

The community project's current plugin targets .NET 8/Rhino 8 libraries. This small bridge instead uses your installed Rhino 7 scripting interface; it does not install that plugin binary.
