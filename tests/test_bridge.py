"""MCP discovery and mocked COM tests; these never modify a live Rhino model."""
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge_worker

class MCPDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_tool_discovery(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        expected = {'rhino7_status','rhino7_list_objects','rhino7_create_box','rhino7_run_python','rhino7_capture_viewport','gh7_status','gh7_new_document','gh7_search_components','gh7_add_component','gh7_add_slider','gh7_set_slider','gh7_connect','gh7_solve','gh7_save_document'}
        params = StdioServerParameters(command=sys.executable,args=[str(ROOT/'server.py')])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                result = await session.list_tools()
                self.assertEqual({t.name for t in result.tools},expected)
                schema = {t.name:t.inputSchema for t in result.tools}
                self.assertTrue({'name','minimum','maximum','value','x','y'} <= set(schema['gh7_add_slider']['required']))
                self.assertEqual(schema['gh7_save_document']['required'],['path'])

class WorkerTests(unittest.TestCase):
    def run_worker(self,processes=None,grasshopper=False,respond=True):
        pythoncom=ModuleType('pythoncom');pythoncom.CoInitialize=Mock();pythoncom.CoUninitialize=Mock()
        win32com=ModuleType('win32com');client=ModuleType('win32com.client');win32com.client=client
        rhino=SimpleNamespace(IsInitialized=True,ReleaseWithoutClosing=Mock());client.Dispatch=Mock(return_value=rhino)
        with tempfile.TemporaryDirectory(prefix="rhino user's ") as folder:
            request=Path(folder)/'request.json';response=Path(folder)/'response.json'
            request.write_text(json.dumps({'parameters':{},'body':"result = {'text': u'QR \\u00b2 caf\\u00e9'}",'grasshopper':grasshopper}),encoding='utf-8')
            commands=[]
            def run_script(command,echo):
                commands.append(command)
                if command.startswith('_-RunPythonScript') and respond:
                    response.write_text(json.dumps({'ok':True,'result':{'text':'QR ² café'}},ensure_ascii=False),encoding='utf-8')
                return 1
            rhino.RunScript=Mock(side_effect=run_script)
            if processes is None:processes=[{'Id':123,'Path':r'C:\Program Files\Rhino 7\System\Rhino.exe'}]
            with patch.dict(sys.modules,{'pythoncom':pythoncom,'win32com':win32com,'win32com.client':client}):
                with patch.object(bridge_worker.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps(processes))):
                    bridge_worker.worker_main(str(request),str(response))
            script=request.with_suffix('.py')
            return {'reply':json.loads(response.read_text(encoding='utf-8')),'commands':commands,'script':script.read_text(encoding='utf-8') if script.exists() else '', 'released':rhino.ReleaseWithoutClosing.call_count,'dispatches':client.Dispatch.call_count,'uninitialized':pythoncom.CoUninitialize.call_count}

    def test_unicode_wrapper_quoted_paths_and_cleanup(self):
        r=self.run_worker()
        self.assertEqual(r['reply']['result']['text'],'QR ² café')
        self.assertTrue(r['commands'][0].startswith('_-RunPythonScript "'))
        self.assertTrue(r['commands'][0].endswith('request.py"'))
        self.assertIn('ensure_ascii=False',r['script'])
        self.assertIn('System.Text.UTF8Encoding(False)',r['script'])
        self.assertIn('System.Single, System.Double, System.Decimal',r['script'])
        self.assertEqual((r['released'],r['uninitialized']),(1,1))

    def test_grasshopper_opens_before_script(self):
        r=self.run_worker(grasshopper=True)
        self.assertEqual(r['commands'][0],'_Grasshopper')
        self.assertTrue(r['commands'][1].startswith('_-RunPythonScript'))

    def test_multiple_rhino_processes_are_rejected_before_dispatch(self):
        r=self.run_worker(processes=[{'Id':1},{'Id':2}])
        self.assertFalse(r['reply']['ok']);self.assertIn('exactly one Rhino 7',r['reply']['error']);self.assertEqual(r['dispatches'],0)

    def test_other_rhino_versions_are_rejected(self):
        r=self.run_worker(processes=[{'Id':1,'Path':r'C:\Program Files\Rhino 8\System\Rhino.exe'}])
        self.assertFalse(r['reply']['ok']);self.assertIn('not Rhino 7',r['reply']['error'])

    def test_missing_response_does_not_retry(self):
        r=self.run_worker(respond=False)
        self.assertFalse(r['reply']['ok']);self.assertIn('did not execute the script',r['reply']['error']);self.assertEqual(len(r['commands']),1);self.assertEqual(r['released'],1)

if __name__=='__main__':unittest.main()
