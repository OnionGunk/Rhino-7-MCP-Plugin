"""Rhino 7 COM worker; independent of the MCP server and its standard streams."""
import json, subprocess, sys, textwrap
from pathlib import Path

def worker_main(request_path: str, response_path: str) -> None:
    progress = Path(response_path).with_suffix('.progress')
    progress.write_text('import COM', encoding='utf-8')
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    progress.write_text('inspect Rhino process', encoding='utf-8')
    rhino = None
    response = Path(response_path)
    try:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        inspection = subprocess.run([
            "powershell.exe", "-NoProfile", "-Command",
            "@(Get-Process Rhino -ErrorAction SilentlyContinue | Select-Object Id,Path) | ConvertTo-Json -Compress"
        ], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        processes = json.loads(inspection.stdout or "[]")
        if isinstance(processes, dict): processes = [processes]
        if len(processes) != 1:
            raise RuntimeError("Keep exactly one Rhino 7 window running so the bridge can target it unambiguously.")
        executable = str(processes[0].get("Path") or "")
        if "rhino 7" not in executable.lower():
            raise RuntimeError("The running Rhino process is not Rhino 7.")
        progress.write_text('connect Rhino COM', encoding='utf-8')
        rhino = win32com.client.Dispatch("Rhino.Interface.7")
        progress.write_text('check initialization', encoding='utf-8')
        if not rhino.IsInitialized:
            raise RuntimeError("Rhino 7 is still initializing. Finish opening it first.")
        if request.get("grasshopper"):
            rhino.RunScript("_Grasshopper", 0)
        progress.write_text('write Rhino script', encoding='utf-8')
        script_path = Path(request_path).with_suffix(".py")
        script = (
            "# -*- coding: utf-8 -*-\n"
            "import json, traceback, System, Rhino, os\n"
            "import rhinoscriptsyntax as rs\n"
            "import scriptcontext as sc\n"
            "parameters = json.loads(" + repr(json.dumps(request["parameters"])) + ")\n"
            "_response_path = " + repr(str(response)) + "\n"
            "def _json_ready(value):\n"
            "    if isinstance(value, (System.Single, System.Double, System.Decimal)): return float(value)\n"
            "    if isinstance(value, dict): return dict((_json_ready(k), _json_ready(v)) for k,v in value.items())\n"
            "    if isinstance(value, (list, tuple)): return [_json_ready(v) for v in value]\n"
            "    if isinstance(value, System.String): return unicode(value)\n"
            "    return value\n"
            "result = None\n"
            "try:\n"
            "    if Rhino.RhinoApp.ExeVersion != 7: raise Exception('Rhino 7 is required.')\n"
            + textwrap.indent(request["body"].strip() + "\n", "    ")
            + "    _reply = {'ok': True, 'result': result}\n"
            "except Exception as _error:\n"
            "    _reply = {'ok': False, 'error': str(_error), 'traceback': traceback.format_exc()}\n"
            "try:\n"
            "    _encoded = json.dumps(_json_ready(_reply), ensure_ascii=False)\n"
            "except Exception as _serialization_error:\n"
            "    _detail = traceback.format_exc().encode('ascii', 'backslashreplace').decode('ascii')\n"
            "    _encoded = json.dumps({'ok': False, 'error': 'JSON serialization: ' + _detail})\n"
            "System.IO.File.WriteAllText(_response_path + '.tmp', _encoded, System.Text.UTF8Encoding(False))\n"
            "os.rename(_response_path + '.tmp', _response_path)\n"
        )
        script_path.write_text(script, encoding="utf-8")
        progress.write_text('execute Rhino script', encoding='utf-8')
        success = rhino.RunScript('_-RunPythonScript "' + str(script_path) + '"', 0)
        if not response.exists():
            raise RuntimeError("Rhino did not execute the script. Finish any active command or dialog, then retry. RunScript=" + str(success))
    except Exception as exc:
        response.write_text(json.dumps({"ok": False, "error": str(exc)}), encoding="utf-8")
    finally:
        if rhino is not None:
            try:
                rhino.ReleaseWithoutClosing()
            except Exception:
                pass
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    worker_main(sys.argv[1], sys.argv[2])
