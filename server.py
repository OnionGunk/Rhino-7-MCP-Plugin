"""Local MCP bridge for Windows Rhino 7, using its documented COM interface.

No Rhino plugin, network listener, API key, or Rhino upgrade is required.
The helper runs each command on Rhino's own script/UI thread.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parent
SESSION = ROOT / "sessions"
SESSION.mkdir(exist_ok=True)
CALL_LOCK = threading.Lock()
mcp = FastMCP("Rhino 7 Live", instructions=(
    "Tools operate on the single running Windows Rhino 7 session. "
    "Inspect rhino7_status and gh7_status before changes. Keep existing models "
    "and definitions; use a new named Grasshopper document for new work. "
    "Use native Grasshopper components for inspectable parametric designs. "
    "rhino7_run_python uses IronPython 2.7, not Python 3. "
    "Do not automatically retry a timeout: the operation may have completed."
))
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def run_script(body: str, parameters: dict | None = None, grasshopper: bool = False) -> dict:
    with CALL_LOCK:
        return _run_script(body, parameters, grasshopper)


def _run_script(body: str, parameters: dict | None = None, grasshopper: bool = False) -> dict:
    """Isolate COM in a worker so a Rhino modal dialog cannot hang the MCP host."""
    with tempfile.TemporaryDirectory(prefix="call-", dir=SESSION, ignore_cleanup_errors=True) as directory:
        directory = Path(directory)
        request = directory / "request.json"
        response = directory / "response.json"
        request.write_text(json.dumps({"body": body, "parameters": parameters or {},
                                      "grasshopper": grasshopper}), encoding="utf-8")
        try:
            worker = subprocess.run([sys.executable, str(ROOT / "bridge_worker.py"),
                                     str(request), str(response)],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=45,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Rhino did not respond within 45 seconds. The operation's "
                               "outcome is unknown; inspect Rhino before trying again.") from exc
        if not response.exists():
            raise RuntimeError("Rhino bridge worker did not return a result: " + worker.stderr[-500:])
        data = json.loads(response.read_text(encoding="utf-8-sig"))
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "Rhino operation failed."))
        return data.get("result")




GH = r"""
import clr
clr.AddReferenceToFileAndPath(r'C:\Program Files\Rhino 7\Plug-ins\Grasshopper\Grasshopper.dll')
import Grasshopper as gh
from Grasshopper.Kernel import GH_Document, IGH_Component, IGH_Param
from Grasshopper.Kernel.Special import GH_NumberSlider
from System.Drawing import PointF
doc = gh.Instances.ActiveCanvas.Document if gh.Instances.ActiveCanvas else None
"""
GH_REQUIRE = GH + "\nif doc is None: raise Exception('Open a Grasshopper definition or call gh7_new_document first.')\n"


@mcp.tool(annotations=READ)
def rhino7_status() -> dict:
    """Read the active Rhino 7 model, units, object count, and application version."""
    return run_script("""
doc = Rhino.RhinoDoc.ActiveDoc
result = {'version': str(Rhino.RhinoApp.Version), 'name': doc.Name,
          'path': doc.Path, 'units': str(doc.ModelUnitSystem),
          'object_count': doc.Objects.Count, 'process_id': System.Diagnostics.Process.GetCurrentProcess().Id}
""")


@mcp.tool(annotations=READ)
def rhino7_list_objects(limit: int = 100) -> dict:
    """List model objects with IDs, names, layers, types, and bounding boxes."""
    return run_script("""
doc = Rhino.RhinoDoc.ActiveDoc
items = []
for obj in doc.Objects:
    if len(items) >= max(1, min(int(parameters['limit']), 500)): break
    box = obj.Geometry.GetBoundingBox(True)
    items.append({'id': str(obj.Id), 'name': obj.Attributes.Name,
                  'layer': doc.Layers[obj.Attributes.LayerIndex].FullPath,
                  'type': str(obj.ObjectType),
                  'bounds': [[box.Min.X, box.Min.Y, box.Min.Z], [box.Max.X, box.Max.Y, box.Max.Z]]})
result = {'objects': items, 'total': doc.Objects.Count}
""", {"limit": limit})


@mcp.tool(annotations=WRITE)
def rhino7_create_box(x: float = 0, y: float = 0, z: float = 0,
                     width: float = 10, depth: float = 10, height: float = 10,
                     name: str = "Citycode building", layer: str = "Citycode") -> dict:
    """Create a named solid box on a dedicated layer in the active Rhino model."""
    return run_script("""
from Rhino.Geometry import Box, Plane, Interval, Point3d
from Rhino.DocObjects import ObjectAttributes
import math
p = parameters
for key in ['x','y','z','width','depth','height']:
    if math.isnan(p[key]) or math.isinf(p[key]): raise Exception('Values must be finite.')
if min(p['width'], p['depth'], p['height']) <= 0: raise Exception('Box dimensions must be positive.')
doc = Rhino.RhinoDoc.ActiveDoc
undo = doc.BeginUndoRecord('Citycode bridge: create box')
try:
    layer_id = doc.Layers.FindByFullPath(p['layer'], -1)
    if layer_id < 0: layer_id = doc.Layers.Add(p['layer'], System.Drawing.Color.FromArgb(166, 88, 60))
    attributes = ObjectAttributes()
    attributes.LayerIndex = layer_id
    attributes.Name = p['name']
    box = Box(Plane(Point3d(p['x'],p['y'],p['z']), Rhino.Geometry.Vector3d.ZAxis),
              Interval(0,p['width']), Interval(0,p['depth']), Interval(0,p['height']))
    object_id = doc.Objects.AddBrep(box.ToBrep(), attributes)
    if object_id == System.Guid.Empty: raise Exception('Rhino could not add the box.')
    doc.Views.Redraw()
    result = {'id': str(object_id), 'name': p['name'], 'layer': p['layer']}
finally:
    doc.EndUndoRecord(undo)
""", dict(x=x,y=y,z=z,width=width,depth=depth,height=height,name=name,layer=layer))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def rhino7_run_python(code: str) -> dict:
    """Run IronPython 2.7 in the current Rhino 7 session. Assign JSON-compatible output to result.

    Rhino, rs (rhinoscriptsyntax), sc (scriptcontext), and System are pre-imported.
    Use this for modeling or Grasshopper operations not covered by the focused tools.
    Inspect current documents first. Never delete or overwrite user work without authorization.
    """
    return run_script(code)


@mcp.tool(annotations=READ)
def rhino7_capture_viewport(width: int = 1000, height: int = 700) -> Image:
    """Return a PNG of the active Rhino viewport without interacting with desktop input."""
    import base64
    data = run_script("""
view = Rhino.RhinoDoc.ActiveDoc.Views.ActiveView
if view is None: raise Exception('No active viewport.')
capture = Rhino.Display.ViewCapture()
capture.Width = max(200, min(parameters['width'], 2000))
capture.Height = max(200, min(parameters['height'], 1600))
capture.ScaleScreenItems = False
capture.DrawAxes = False
capture.DrawGrid = False
capture.DrawGridAxes = False
image = capture.CaptureToBitmap(view)
if image is None: raise Exception('Viewport capture failed.')
stream = System.IO.MemoryStream()
try:
    image.Save(stream, System.Drawing.Imaging.ImageFormat.Png)
    result = {'png': System.Convert.ToBase64String(stream.ToArray())}
finally:
    image.Dispose()
    stream.Dispose()
""", dict(width=width,height=height))
    return Image(data=base64.b64decode(data["png"]), format="png")


@mcp.tool(annotations=READ)
def gh7_status() -> dict:
    """Inspect the active Grasshopper 1 definition, nodes, sliders, and component errors."""
    return run_script(GH + """
items = []
if doc is not None:
    for obj in doc.Objects:
        item = {'id': str(obj.InstanceGuid), 'component_guid': str(obj.ComponentGuid),
                'name': obj.Name, 'nickname': obj.NickName,
                'position': [float(obj.Attributes.Pivot.X), float(obj.Attributes.Pivot.Y)]}
        if isinstance(obj, GH_NumberSlider): item['value'] = float(obj.CurrentValue)
        if isinstance(obj, IGH_Component):
            item['inputs'] = [{'index': i, 'name': p.Name, 'sources': [str(s.InstanceGuid) for s in p.Sources]} for i,p in enumerate(obj.Params.Input)]
            item['outputs'] = [{'index': i, 'name': p.Name, 'data_count': p.VolatileDataCount} for i,p in enumerate(obj.Params.Output)]
            item['errors'] = list(obj.RuntimeMessages(gh.Kernel.GH_RuntimeMessageLevel.Error))
        items.append(item)
result = {'document': doc.DisplayName if doc else None, 'nodes': items,
          'open_documents': gh.Instances.DocumentServer.DocumentCount}
""", grasshopper=True)


@mcp.tool(annotations=WRITE)
def gh7_new_document(name: str = "Citycode design") -> dict:
    """Create and activate an additional Grasshopper definition, preserving existing definitions."""
    return run_script(GH + """
new_doc = GH_Document()
new_doc.Properties.ProjectFileName = parameters['name']
gh.Instances.DocumentServer.AddDocument(new_doc)
gh.Instances.ActiveCanvas.Document = new_doc
result = {'name': new_doc.DisplayName, 'document_id': str(new_doc.DocumentID)}
""", {"name": name}, grasshopper=True)


@mcp.tool(annotations=READ)
def gh7_search_components(query: str, limit: int = 20) -> dict:
    """Find installed native or plugin Grasshopper components and their creation GUIDs."""
    return run_script(GH + """
items = []
query = parameters['query'].lower()
for proxy in gh.Instances.ComponentServer.ObjectProxies:
    description = proxy.Desc
    if query in (description.Name + ' ' + description.NickName).lower():
        items.append({'guid': str(proxy.Guid), 'name': description.Name,
                      'nickname': description.NickName, 'category': description.Category,
                      'subcategory': description.SubCategory})
    if len(items) >= max(1,min(parameters['limit'],100)): break
result = {'components': items}
""", dict(query=query,limit=limit), grasshopper=True)


@mcp.tool(annotations=WRITE)
def gh7_add_component(component_guid: str, x: float, y: float, nickname: str = "") -> dict:
    """Add an installed component to the active GH canvas using a GUID from component search."""
    return run_script(GH_REQUIRE + """
obj = gh.Instances.ComponentServer.EmitObjectProxy(System.Guid(parameters['component_guid'])).CreateInstance()
if obj is None: raise Exception('Component could not be created.')
obj.CreateAttributes()
obj.Attributes.Pivot = PointF(parameters['x'], parameters['y'])
if parameters['nickname']: obj.NickName = parameters['nickname']
doc.AddObject(obj, False)
result = {'id': str(obj.InstanceGuid), 'name': obj.Name}
if isinstance(obj, IGH_Component):
    result['inputs'] = [{'index': i, 'name': p.Name} for i,p in enumerate(obj.Params.Input)]
    result['outputs'] = [{'index': i, 'name': p.Name} for i,p in enumerate(obj.Params.Output)]
gh.Instances.ActiveCanvas.Invalidate()
""", dict(component_guid=component_guid,x=x,y=y,nickname=nickname), grasshopper=True)


@mcp.tool(annotations=WRITE)
def gh7_add_slider(name: str, minimum: float, maximum: float, value: float,
                   x: float, y: float, decimals: int = 2) -> dict:
    """Create an editable named number slider with explicit range, value, and canvas position."""
    return run_script(GH_REQUIRE + """
p = parameters
if p['minimum'] >= p['maximum'] or not p['minimum'] <= p['value'] <= p['maximum']:
    raise Exception('Slider value must lie inside a valid range.')
obj = GH_NumberSlider()
obj.CreateAttributes()
obj.NickName = p['name']
obj.Slider.Minimum = System.Decimal(p['minimum'])
obj.Slider.Maximum = System.Decimal(p['maximum'])
obj.Slider.DecimalPlaces = max(0, min(p['decimals'], 6))
obj.SetSliderValue(System.Decimal(p['value']))
obj.Attributes.Pivot = PointF(p['x'], p['y'])
doc.AddObject(obj, False)
gh.Instances.ActiveCanvas.Invalidate()
result = {'id': str(obj.InstanceGuid), 'name': obj.NickName, 'value': float(obj.CurrentValue)}
""", dict(name=name,minimum=minimum,maximum=maximum,value=value,x=x,y=y,decimals=decimals), grasshopper=True)


@mcp.tool(annotations=WRITE)
def gh7_set_slider(slider_id: str, value: float) -> dict:
    """Change a slider and recompute the active Grasshopper definition."""
    return run_script(GH_REQUIRE + """
obj = doc.FindObject(System.Guid(parameters['slider_id']), False)
if not isinstance(obj, GH_NumberSlider): raise Exception('ID is not a number slider.')
if not float(obj.Slider.Minimum) <= parameters['value'] <= float(obj.Slider.Maximum):
    raise Exception('Value is outside the slider range.')
obj.SetSliderValue(System.Decimal(parameters['value']))
doc.NewSolution(False)
result = {'id': str(obj.InstanceGuid), 'value': float(obj.CurrentValue)}
""", dict(slider_id=slider_id,value=value), grasshopper=True)


@mcp.tool(annotations=WRITE)
def gh7_connect(source_id: str, target_id: str, input_index: int = 0, output_index: int = 0) -> dict:
    """Wire a component output or slider into a component input; preserve existing sources."""
    return run_script(GH_REQUIRE + """
p = parameters
source = doc.FindObject(System.Guid(p['source_id']), False)
target = doc.FindObject(System.Guid(p['target_id']), False)
if source is None or target is None: raise Exception('Source or target node does not exist.')
output = source.Params.Output[p['output_index']] if isinstance(source, IGH_Component) else source
input_param = target.Params.Input[p['input_index']] if isinstance(target, IGH_Component) else target
if not isinstance(output, IGH_Param) or not isinstance(input_param, IGH_Param):
    raise Exception('Both connection endpoints must be parameters.')
if not input_param.Sources.Contains(output): input_param.AddSource(output)
doc.NewSolution(False)
gh.Instances.ActiveCanvas.Invalidate()
result = {'connected': True}
""", dict(source_id=source_id,target_id=target_id,input_index=input_index,output_index=output_index), grasshopper=True)


@mcp.tool(annotations=WRITE)
def gh7_solve() -> dict:
    """Recompute the current Grasshopper definition and redraw Rhino."""
    return run_script(GH_REQUIRE + """
doc.NewSolution(False)
Rhino.RhinoDoc.ActiveDoc.Views.Redraw()
result = {'solved': True, 'nodes': doc.ObjectCount}
""", grasshopper=True)


@mcp.tool(annotations=WRITE)
def gh7_save_document(path: str) -> dict:
    """Save the active Grasshopper definition to a new .gh path; refuse overwriting files."""
    return run_script(GH_REQUIRE + """
import os
path = parameters['path']
if not os.path.isabs(path) or not path.lower().endswith('.gh'): raise Exception('Use an absolute .gh path.')
if os.path.exists(path): raise Exception('File already exists. Choose a new path.')
archive = gh.Kernel.GH_DocumentIO(doc)
if not archive.SaveQuiet(path): raise Exception('Grasshopper could not save the definition.')
result = {'path': path, 'saved': True}
""", dict(path=path), grasshopper=True)


if __name__ == "__main__":
    mcp.run(transport="stdio")
