import os,json,tempfile
from pathlib import Path
from unittest.mock import MagicMock,patch
root=Path(tempfile.mkdtemp(prefix='bauer-web-history-review-'))
os.environ.update(BAUER_HOME=str(root/'home'),BAUER_CONFIG=str(root/'missing.yaml'),BAUER_AGENTS_FILE=str(root/'missing-agents.yaml'))
from fastapi.testclient import TestClient
from bauer.server import create_app
from bauer.tool_router import ToolRouter
from bauer.session_store import SessionStore
ws=root/'workspace';ws.mkdir()
cfg=root/'config.yaml';cfg.write_text('model:\n  provider: ollama\n  name: fake\n  requested_context: 4096\nkernel:\n  enabled: true\n')
llm=MagicMock();llm.host='http://localhost:11434';llm._provider='ollama';llm.list_models.return_value=['fake']
seen=[]
def fake_turn(ctx,*a,**k):
 seen.append([dict(m) for m in ctx.messages]);reply='answer-'+ctx.messages[-1]['content'];ctx.add_assistant(reply);return reply,[]
with patch('bauer.agent.run_one_turn_with_fallback',side_effect=fake_turn),patch('bauer.agent.run_one_turn',side_effect=fake_turn):
 app=create_app('fake',4096,ToolRouter(workspace=ws,audit_enabled=False),llm,'test',root/'sessions',api_key='audit-key',rate_limit_requests=0,workspace=ws,config_path=cfg)
 tc=TestClient(app);headers={'X-API-Key':'audit-key'}
 (root/'victim.jsonl').write_text(json.dumps({'role':'user','content':'outside-original'})+'\n')
 resp=tc.post('/chat',headers=headers,json={'message':'overwrite','session_id':'../victim'})
 print('TRAVERSAL_KERNEL_ENABLED',resp.status_code,resp.json()['response'])
 print('TRAVERSAL_STORE',SessionStore(root/'sessions').load('../victim'))
 for msgs in [[{'role':'user','content':'first'}],[{'role':'user','content':'first'},{'role':'assistant','content':'answer-first'},{'role':'user','content':'second'}]]:
  resp=tc.post('/v1/chat/completions',headers=headers,json={'session_id':'review','messages':msgs})
  print('V1_RESULT',resp.status_code,resp.json()['choices'][0]['message'])
 print('V1_PROVIDER_SECOND_CONTEXT',seen[-1])
 print('V1_STORE',SessionStore(root/'sessions').load('review'))
print('ROOT',root)
