from pathlib import Path
exec(Path('/tmp/bauer_audit_tools_probe.py').read_text())
from bauer.tool_router import _TOOL_SECURITY, _policy_operation_for_tool
from bauer.tools.base import SandboxError
# Metadata drift inventory.
print('SECURITY_MISSING',sorted(set(r._tools)-set(_TOOL_SECURITY)))
print('SECURITY_ORPHANS',sorted(set(_TOOL_SECURITY)-set(r._tools)))
# internal state files do not apply _sandbox; symlink follows out of workspace.
linkws=root/'links';linkws.mkdir(); outside=root/'outside-memory.json';outside.write_text('{}')
(linkws/'.bauer_memory.json').symlink_to(outside)
lr=ToolRouter(workspace=linkws,audit_enabled=False)
try:lr._sandbox('.bauer_memory.json')
except SandboxError:print('SANDBOX_SYMLINK_BLOCKED',True)
lr.execute({'action':'memory','args':{'action':'set','key':'audit','value':'wrote-outside'}})
print('MEMORY_SYMLINK_OUTSIDE_WRITE',json.loads(outside.read_text())['audit']['value'])
# Dynamic tool approval is never consulted, even with llm_client.
ext.register('approval_audit',lambda a:'ran',description='critical',args={},permission='system',risk='critical',requires_approval=True)
lr._llm_client=object()
with patch('bauer.llm_approval.llm_evaluate_tool') as approver:
 print('EXTERNAL_WITH_LLM',lr.execute({'action':'approval_audit','args':{}}))
 print('EXTERNAL_APPROVER_CALL_COUNT',approver.call_count)
ext.clear()
# Fake transport demonstrates policy deny(network.http) has no enforcement on http_request.
polws=root/'policy-net';polws.mkdir(); (polws/'.bauer').mkdir()
(polws/'.bauer'/'policy.yaml').write_text('rules:\n  - id: deny-all-http\n    operation: network.http\n    action: deny\n')
net=ToolRouter(workspace=polws,web_enabled=True,audit_enabled=False,policy_enabled=True,policy_root=root/'runtime-net')
import httpx
calls=[]
def request_stub(method,url,**kwargs):
 calls.append((method,url,kwargs))
 if len(calls)==1:
  return httpx.Response(302,headers={'location':'https://other.example/target'},request=httpx.Request(method,url))
 return httpx.Response(200,headers={'content-type':'text/plain'},text='ok',request=httpx.Request(method,url))
with patch('bauer.tools.web._is_safe_url'),patch('httpx.request',request_stub):
 output=net.execute({'action':'http_request','args':{'url':'https://source.example/start','headers':{'Authorization':'Bearer audit-token'},'contains_secret':True}})
print('HTTP_POLICY_OPERATION',_policy_operation_for_tool('http_request'))
print('HTTP_DENY_RULE_CALL_COUNT',len(calls))
print('HTTP_REDIRECT_FOREIGN_AUTH',calls[1][2]['headers'].get('Authorization'))
print('HTTP_OUTPUT',output.replace('\n',' | '))
# send_message permits paths outside workspace even though read_file rejects those paths.
class Bridge:
 def send_media(self,chat_id,path):
  self.path=path;return True
bridge=Bridge(); media=root/'outside-private.txt';media.write_text('audit-only-private')
with patch('bauer.live_bridges.get',lambda channel:bridge):
 print('SEND_MEDIA_OUTSIDE',r.execute({'action':'send_message','args':{'channel':'telegram','chat_id':'audit','media_path':str(media)}}))
 print('SEND_MEDIA_RECEIVED_ABS_PATH',bridge.path)
