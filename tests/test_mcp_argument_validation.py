"""native MCPの誤った成功包装より前に引数不正を拒否するC#式を検証します。"""
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from test_gateway_limits_telemetry import PWSH

class McpArgumentValidationTests(unittest.TestCase):
    def test_public_argument_schema_without_reflecting_sensitive_input(self):
        if not PWSH:self.skipTest('PowerShell Roslyn is required')
        root=Path(__file__).resolve().parents[1]
        api=json.loads((root/'apps/gateway/apim/apis/pii-v1.json').read_text(encoding='utf-8'))
        value=ET.parse(root/'apps/gateway/apim/policies/mcp.xml').find("./inbound/set-variable[@name='gatewayMcpArgumentError']").get('value')
        expression=value[2:-1].replace('__AUTHORIZATION_BASE64__',base64.b64encode(json.dumps(api['authorization']).encode()).decode())
        def call(arguments):return {'jsonrpc':'2.0','id':17,'method':'tools/call','params':{'name':'pii_redact_text','arguments':arguments}}
        valid=call({'text':'hello','language':'en'})
        cases=[valid,call({'text':1,'language':'en'}),call({'text':'SECRET_INPUT','language':'unknown'}),call({'text':'','language':'en'}),call({'text':'hello','language':'en','extra':'SECRET_INPUT'}),call({'language':'ja'}),call({'text':'x'*32768,'language':'en'}),{'jsonrpc':'2.0','id':1,'method':'tools/list'},call({'text':'😀'*5120,'language':'en'}),call({'text':'e\u0301'*5120,'language':'en'})]
        source=r'''
using System;using System.Linq;using System.Text;using Newtonsoft.Json;using Newtonsoft.Json.Linq;
public class Body {public string Data;public T As<T>(bool preserveContent=false){return (T)(object)JObject.Parse(Data);}}
public class Request {public Body Body=new Body();}
public class Context {public Request Request=new Request();}
public static class McpFixture {
 static string Check(Context context){__EXPRESSION__}
 public static string Run(string json){var result=new JArray();foreach(var item in JArray.Parse(json)){var c=new Context();c.Request.Body.Data=item.ToString(Formatting.None);result.Add(Check(c));}return result.ToString(Formatting.None);}
}'''.replace('__EXPRESSION__',expression)
        with tempfile.TemporaryDirectory()as folder:
            p=Path(folder);(p/'fixture.cs').write_text(source,encoding='utf-8');(p/'cases.json').write_text(json.dumps(cases),encoding='utf-8')
            (p/'run.ps1').write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll')|ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[McpFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""",encoding='utf-8')
            result=subprocess.run([PWSH,'-NoProfile','-File',str(p/'run.ps1')],text=True,capture_output=True,encoding='utf-8',timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        results=json.loads(result.stdout)
        for i in [0,7,8,9]:self.assertEqual(results[i],'')
        for i in [1,2,3,4,5,6]:
            message=json.loads(results[i]);self.assertEqual(message,{'jsonrpc':'2.0','id':17,'error':{'code':-32602,'message':'Invalid tool arguments'}})
            self.assertNotIn('SECRET_INPUT',results[i])

if __name__=='__main__':unittest.main()
