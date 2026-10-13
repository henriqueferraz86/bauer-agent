import os,json,tempfile,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock,patch
root=Path(tempfile.mkdtemp(prefix='bauer-web-audit2-'))
os.environ.update(BAUER_HOME=str(root/'home'),BAUER_CONFIG=str(root/'missing.yaml'),BAUER_AGENTS_FILE=str(root/'missing-agents.yaml'))
from fastapi.testclient import TestClient
from bauer.server import create_app
from bauer.tool_router import ToolRouter
from bauer.session_store import SessionStore
ws=root/'workspace';ws.mkdir();cfg=root/'config.yaml';cfg.write_text('model:\n  provider: ollama\n  name: fake\n  requested_context: 4096\nkernel:\n  enabled: false\n')
llm=MagicMock();llm.host='http://localhost:11434';llm._provider='ollama';llm.list_models.return_value=['fake'];llm.chat.return_value='RESPOSTA'
def fake_turn(ctx,*a,**k):ctx.add_assistant('RESPOSTA');return 'RESPOSTA',[]
with patch('bauer.agent.run_one_turn',side_effect=fake_turn),patch('bauer.agent.run_one_turn_with_fallback',side_effect=fake_turn):
    app=create_app('fake',4096,ToolRouter(workspace=ws),llm,'test',root/'sessions',rate_limit_requests=0,workspace=ws,config_path=cfg,profile_set='local')
client=TestClient(app)
cloud=MagicMock();cloud._provider='openai'
with patch('bauer.auxiliary_client._build_client_for_provider',return_value=cloud):
    r=client.post('/models/switch',json={'provider':'openai','model':'fake-cloud'})
    print('LOCAL_SWITCH',r.status_code,r.json(),client.get('/status').json()['provider'])
# independent app captures gated turn to deterministically overlap two requests.
a_started=threading.Event();b_started=threading.Event();release_a=threading.Event();release_b=threading.Event()
def overlap_turn(ctx,*args,**kwargs):
    user=ctx.messages[-1]['content'];(a_started if user=='A' else b_started).set()
    (release_a if user=='A' else release_b).wait(10)
    ctx.add_assistant('answer-'+user);return 'answer-'+user,[]
with patch('bauer.agent.run_one_turn_with_fallback',side_effect=overlap_turn):
    app2=create_app('fake',4096,ToolRouter(workspace=ws),llm,'test',root/'concurrent-sessions',rate_limit_requests=0,workspace=ws)
client2=TestClient(app2)
with ThreadPoolExecutor(max_workers=2) as pool:
    first=pool.submit(client2.post,'/chat',json={'message':'A','session_id':'shared'})
    assert a_started.wait(10)
    second=pool.submit(client2.post,'/chat',json={'message':'B','session_id':'shared'})
    assert b_started.wait(10)
    release_a.set();ra=first.result(10)
    release_b.set();rb=second.result(10)
    print('CONCURRENT_REQUESTS',ra.status_code,rb.status_code)
    print('CONCURRENT_HISTORY',SessionStore(root/'concurrent-sessions').load('shared'))
print('ROOT',root)
