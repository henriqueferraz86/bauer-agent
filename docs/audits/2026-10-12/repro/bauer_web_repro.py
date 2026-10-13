import os,json,tempfile
from pathlib import Path
from unittest.mock import MagicMock,patch
root=Path(tempfile.mkdtemp(prefix='bauer-web-audit-'))
os.environ.update(BAUER_HOME=str(root/'home'),BAUER_CONFIG=str(root/'missing.yaml'),BAUER_AGENTS_FILE=str(root/'missing-agents.yaml'))
from fastapi.testclient import TestClient
from bauer.server import create_app
from bauer.tool_router import ToolRouter
from bauer.session_store import SessionStore
llm=MagicMock();llm.host='http://localhost:11434';llm._provider='ollama';llm.list_models.return_value=['fake'];llm.chat.return_value='RESPOSTA';llm.chat_stream.return_value=iter(['RESPOSTA'])
workspace=root/'workspace';workspace.mkdir()
app=create_app('fake',4096,ToolRouter(workspace=workspace),llm,'test',root/'sessions',api_key='audit-key',rate_limit_requests=0,workspace=workspace)
client=TestClient(app)
def fake_turn(ctx,*a,**k):
    ctx.add_assistant('RESPOSTA');return 'RESPOSTA',[]
(root/'victim.jsonl').write_text(json.dumps({'role':'user','content':'outside-original'})+'\n')
with patch('bauer.agent.run_one_turn_with_fallback',side_effect=fake_turn),patch('bauer.agent.run_one_turn',side_effect=fake_turn):
    res=client.post('/chat',headers={'X-API-Key':'audit-key'},json={'message':'overwrite','session_id':'../victim'})
    print('TRAVERSAL_HTTP',res.status_code, (root/'victim.jsonl').read_text().strip())
setup=client.post('/auth/setup',json={'email':'a@example.com','password':'audit password twelve','bootstrap_key':'audit-key'})
print('SETUP',setup.status_code)
print('CSRF_STREAM',client.get('/stream',params={'message':'test'}).status_code)
team=MagicMock();team.to_dict.return_value={};orch=MagicMock();orch.team_registry.get.return_value=team;orch.stream.return_value=iter([{'event':'executed'}])
with patch('bauer.core.runtime.AgnoTeamOrchestrator',return_value=orch):
    res=client.get('/api/teams/fake/stream',params={'task':'execute tools'})
    print('CSRF_TEAM_STREAM',res.status_code,orch.stream.call_count,res.text.strip())
with patch('bauer.agent.run_one_turn',side_effect=fake_turn):
    for msgs in [[{'role':'user','content':'first'}],[{'role':'user','content':'first'},{'role':'assistant','content':'RESPOSTA'},{'role':'user','content':'second'}]]:
        r=client.post('/v1/chat/completions',headers={'X-API-Key':'audit-key'},json={'model':'fake','session_id':'oai-history','messages':msgs})
        print('OAI_REQUEST',r.status_code)
    print('OAI_HISTORY',SessionStore(root/'sessions').load('oai-history'))
print('ROOT',root)
