"""Hermetic /v1 batch/stream parity proof; no network or credential reads."""
import copy,json,os,sqlite3,tempfile
from pathlib import Path
from unittest.mock import patch
root=Path(tempfile.mkdtemp(prefix='bauer-oai-parity-'))
os.environ.update(BAUER_HOME=str(root/'home'),BAUER_CONFIG=str(root/'missing-config.yaml'),BAUER_AGENTS_FILE=str(root/'missing-agents.yaml'))
from fastapi.testclient import TestClient
from bauer.openai_client import OpenAIClient
from bauer.server import create_app
from bauer.tool_router import ToolRouter
from bauer.core.runtime.autonomy import BudgetManager
class SpyNativeClient(OpenAIClient):
    def __init__(self):
        super().__init__(host='https://api.openai.invalid',api_key='synthetic-audit-only',model='gpt-4o')
        self._provider='openai';self.calls=[];self.native_count=0
    def list_models(self):return ['gpt-4o']
    def has_model(self,name):return True
    def chat_with_tools(self,model,messages,tools,tool_choice='auto',**kwargs):
        self.calls.append({'method':'chat_with_tools','model':model,'messages':copy.deepcopy(messages),'tools':[t['function']['name'] for t in tools],'kwargs':copy.deepcopy(kwargs),'tool_choice':tool_choice})
        self.last_usage={'prompt_tokens':1000,'completion_tokens':100}
        self.native_count+=1
        if self.native_count==1:
            return {'content':'','tool_calls':[{'id':'audit-read','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':'marker.txt'})}}]}
        return {'content':'BATCH_TOOL_OK','tool_calls':[]}
    def chat_stream(self,model,messages,**kwargs):
        self.calls.append({'method':'chat_stream','model':model,'messages':copy.deepcopy(messages),'tools':[],'kwargs':copy.deepcopy(kwargs)})
        self.last_usage={'prompt_tokens':1000,'completion_tokens':100}
        yield 'STREAM_NO_TOOLS'
    def chat_with_retry(self,*a,**kw):raise AssertionError('unexpected bridge retry')
ws=root/'workspace';ws.mkdir();(ws/'marker.txt').write_text('AUDIT_MARKER_READ_ONLY')
results=[]
# Repeat with kernel enabled and disabled; only temporary config paths.
for enabled in [False,True]:
    for stream in [False,True]:
        label=f'kernel-{enabled}-stream-{stream}';work=root/label;work.mkdir()
        cfg=work/'config.yaml';cfg.write_text(f'model:\n  provider: openai\n  name: gpt-4o\n  requested_context: 4096\nkernel:\n  enabled: {str(enabled).lower()}\n')
        spy=SpyNativeClient();router=ToolRouter(workspace=ws)
        app=create_app('gpt-4o',4096,router,spy,'SERVER_SYSTEM_SENTINEL',work/'sessions',config_path=cfg,workspace=ws,rate_limit_requests=0)
        payload={'model':'CLIENT_REQUESTED_MODEL','messages':[{'role':'system','content':'CLIENT_SYSTEM_SENTINEL'},{'role':'user','content':'Read marker.txt using read_file'}],'stream':stream,'max_tokens':7,'temperature':0.123,'session_id':'audit-session'}
        def no_network(*args,**kwargs):raise AssertionError('network forbidden')
        with patch('httpx.get',side_effect=no_network),patch('httpx.post',side_effect=no_network),patch('httpx.stream',side_effect=no_network):
            res=TestClient(app).post('/v1/chat/completions',json=payload)
        rid=res.headers.get('X-Bauer-Run-ID')
        costs=BudgetManager(root=work/'runtime').store.list('run_costs')
        with sqlite3.connect(work/'runtime'/'budget_ledger.sqlite3') as conn:
            ledger=conn.execute('SELECT run_id,actual_usd,state FROM budget_runs').fetchall()
        runs=TestClient(app).get('/runs').json()['runs']
        result={'label':label,'status':res.status_code,'response':res.text,'calls':spy.calls,'costs':costs,'ledger':ledger,'runs':[{k:r.get(k) for k in ['id','status','cost_estimate','tool_calls_count']} for r in runs]}
        results.append(result)
        summary={k:v for k,v in result.items() if k not in ['calls','response','costs']}
        summary['client_methods']=[c['method'] for c in spy.calls]
        summary['tools_schema_counts']=[len(c['tools']) for c in spy.calls]
        summary['client_models']=[c['model'] for c in spy.calls]
        summary['client_system_present']=any('CLIENT_SYSTEM_SENTINEL' in json.dumps(c['messages']) for c in spy.calls)
        summary['parameters_passed']=[c['kwargs'] for c in spy.calls]
        summary['response']=res.text[:260]
        print(json.dumps(summary,ensure_ascii=False))
(root/'proof.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print('ARTIFACT',root/'proof.json')
