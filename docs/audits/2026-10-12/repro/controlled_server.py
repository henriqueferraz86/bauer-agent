import json,os,time
from pathlib import Path
ROOT=Path('/tmp/bauer-audit-20261012/controlled');ROOT.mkdir(exist_ok=True)
os.environ.update(BAUER_HOME=str(ROOT/'home'),BAUER_CONFIG=str(ROOT/'config.yaml'),BAUER_AGENTS_FILE=str(ROOT/'no-agents.yaml'))
from bauer.ollama_client import OllamaClient,OllamaError
from bauer.config_loader import load_config
from bauer.commands._runtime import _build_router
from bauer.agent import _build_system_prompt
from bauer.server import create_app
import uvicorn
WS=ROOT/'workspace';WS.mkdir(exist_ok=True);(ROOT/'home').mkdir(exist_ok=True)
cfg={'model':{'provider':'ollama','name':'audit-controlled','requested_context':8192,'minimum_context':4096},'tools':{'tool_allowlist':['read_file','write_file','list_dir','calculate'],'shell_enabled':False,'web_enabled':False,'max_tool_turns':8},'agent':{'workspace':str(WS),'specialist_delegation':False,'skill_auto_inject':False},'serve':{'rate_limit_requests':0},'kernel':{'enabled':True},'logging':{'level':'debug','file':str(ROOT/'bauer.log')}}
(ROOT/'config.yaml').write_text(json.dumps(cfg));(ROOT/'home'/'config.yaml').write_text(json.dumps(cfg))
class ControlledClient(OllamaClient):
 @property
 def supports_native_tools(self):return True
 def is_alive(self):return True,''
 def list_models(self):return ['audit-controlled']
 def has_model(self,name):return name=='audit-controlled'
 def chat_with_tools(self,model,messages,tools=None,on_delta=None):
  latest=next((m.get('content','') for m in reversed(messages) if m.get('role')=='user'),'')
  with (ROOT/'provider-calls.jsonl').open('a') as f:f.write(json.dumps({'latest':latest,'roles':[m['role'] for m in messages],'schema_count':len(tools or [])})+'\n')
  self.last_usage={'prompt_tokens':20,'completion_tokens':10}
  if 'FAIL_TEST' in latest:raise OllamaError('AUDIT_SYNTHETIC_PROVIDER_FAILURE')
  if 'SLOW_TEST' in latest:time.sleep(8)
  if 'LOOP_TEST' in latest:time.sleep(3)
  toolmsgs=[m for m in messages if m.get('role')=='tool']
  last=messages[-1]
  if 'WRITE_READ_TEST' in latest:
   if last.get('role')!='tool':
    return {'role':'assistant','content':'','tool_calls':[{'id':'call-write','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'controlled-output.txt','content':'CONTROLLED_TOOL_OK'})}}]}
   if last.get('tool_call_id')=='call-write':
    return {'role':'assistant','content':'','tool_calls':[{'id':'call-read','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':'controlled-output.txt'})}}]}
   result='CONTROLLED_TOOL_OK'
  else:result='CONTROLLED_RESPONSE_OK'
  if on_delta:on_delta(result)
  return {'role':'assistant','content':result,'tool_calls':[]}
 def chat(self,model,messages,**kw):return 'CONTROLLED_RESPONSE_OK'
 def chat_stream(self,model,messages,**kw):
  if any('SLOW_TEST' in str(m.get('content','')) for m in messages):time.sleep(8)
  yield 'CONTROLLED_RESPONSE_OK'
client=ControlledClient(host='http://127.0.0.1:1');cfgobj=load_config(ROOT/'config.yaml');router=_build_router(cfgobj,WS,llm_client=client)
app=create_app('audit-controlled',8192,router,client,_build_system_prompt(router,tool_mode='native',client=client),ROOT/'sessions',config_path=ROOT/'config.yaml',workspace=WS,rate_limit_requests=0)
uvicorn.run(app,host='127.0.0.1',port=18761)
