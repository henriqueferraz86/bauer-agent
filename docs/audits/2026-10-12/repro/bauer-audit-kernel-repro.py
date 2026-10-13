import json, os, tempfile, time, subprocess
from pathlib import Path
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
sandbox = Path(tempfile.mkdtemp(prefix='bauer-kernel-audit-'))
os.environ.update(BAUER_HOME=str(sandbox/'home'), BAUER_CONFIG=str(sandbox/'absent.yaml'), BAUER_AGENTS_FILE=str(sandbox/'absent-agents.yaml'))
from bauer.core.kernel.kernel import BauerKernel
from bauer.core.kernel.schemas import KernelRequest
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.autonomy import BudgetManager
from bauer.core.runtime.resilience import RuntimeControl, RunHeartbeat
from bauer.core.policy.engine import PolicyEngine
from bauer.core.kernel.evaluator import Evaluator, CallableGate
from bauer.core.kernel.gates.secrets import SecretsGate

def setup(name, **kw):
    root=sandbox/name
    runs=RunManager(root=root)
    budget=BudgetManager(store=runs.store)
    budget.set_profile(daily_budget_usd=100, max_parallel_runs=10)
    policy=PolicyEngine(workspace=root, runtime_root=root, rules=[])
    k=BauerKernel(runs=runs, policy=policy, budget=budget, heartbeat_interval_s=0, **kw)
    return k,runs,budget

k,r,b=setup('parallel')
b.set_profile(max_parallel_runs=1)
called=[]
a=k.execute(KernelRequest(task='x'), executor=lambda p: called.append(1) or {'output':'ok'})
print(json.dumps({'case':'parallel_self_count','status':a.status,'executor_calls':len(called),'error':a.error}))

k,r,b=setup('heartbeat')
b.set_profile(max_parallel_runs=1)
a=r.create_run(session_id='s',status='running')
r.update_run(a.id, updated_at=(datetime.now(UTC)-timedelta(seconds=1000)).isoformat())
# update_run always sets updated_at to now, so insert explicit old snapshot through store.
data=r.get_run(a.id).__dict__.copy(); data['updated_at']=(datetime.now(UTC)-timedelta(seconds=1000)).isoformat(); r.store.upsert('runs',data)
RunHeartbeat(store=r.store).bater(a.id)
try: b.ensure_can_start(); allowed=True
except Exception: allowed=False
print(json.dumps({'case':'fresh_heartbeat_ignored','active_status':r.get_run(a.id).status,'allowed_new_run_at_limit':allowed}))

for terminal in ['failed','cancelled']:
    k,r,b=setup('cost-'+terminal)
    a=k.execute(KernelRequest(task='x',input={'estimated_cost_usd':0.1}),executor=lambda p,t=terminal: {'status':t,'error':'stop','output':'x','cost_estimate':0.4,'tool_calls_count':2})
    print(json.dumps({'case':'spent_cost_'+terminal,'status':a.status,'run_cost':r.get_run(a.run_id).cost_estimate,'budget_used':b.status()['daily']['used_usd']}))

k,r,b=setup('retry-cost')
count=[0]
def retry(p):
    count[0]+=1
    return {'status':'failed' if count[0]==1 else 'completed','error':'retry','output':'ok','cost_estimate':0.4,'tool_calls_count':2,'llm_calls_count':1}
a=k.execute(KernelRequest(task='x',max_retries=1),executor=retry)
saved=r.get_run(a.run_id)
print(json.dumps({'case':'retry_cost_overwrite','attempts':count[0],'expected_cost':0.8,'saved_cost':saved.cost_estimate,'budget_used':b.status()['daily']['used_usd'],'expected_tools':4,'saved_tools':saved.tool_calls_count}))

k,r,b=setup('cancel-overwrite')
def cancel_then_return(p):
    r.cancel_run(p['run_id'])
    return {'output':'ok','cost_estimate':0.1}
a=k.execute(KernelRequest(task='x'),executor=cancel_then_return)
print(json.dumps({'case':'success_overwrites_cancel','final_status':a.status,'events':[e.get('event_type') for e in r.store.list('events') if e.get('run_id')==a.run_id]}))

slow=Evaluator(gates=[CallableGate('delay',lambda req,res: time.sleep(.04) or True)],max_replans=0)
k,r,b=setup('elapsed',evaluator=slow)
a=k.execute(KernelRequest(task='x'),executor=lambda p:{'output':'ok','elapsed_seconds':0.001})
print(json.dumps({'case':'elapsed_gate_time_overwritten','elapsed_seconds':r.get_run(a.run_id).elapsed_seconds,'gate_delay_minimum':0.04}))

k,r,b=setup('continue')
control=RuntimeControl(store=r.store); k.control=control
run,early=k.admit(KernelRequest(task='x'))
control.set_kill_switch(True)
count=[]
a=k.continue_run(run.id,executor=lambda p:count.append(1) or {'output':'ok'})
print(json.dumps({'case':'continue_ignores_kill_switch','kill_switch':control.kill_switch_enabled(),'status':a.status,'executor_calls':len(count)}))

k,r,b=setup('replan')
control=RuntimeControl(store=r.store); k.control=control
checks=[0]
def gate(req,res):
    checks[0]+=1
    if checks[0]==1:
        control.set_kill_switch(True)
        return 'replan please'
    return True
k.evaluator=Evaluator(gates=[CallableGate('gate',gate)],max_replans=1)
count=[]
a=k.execute(KernelRequest(task='x'),executor=lambda p:count.append(1) or {'output':str(len(count))})
print(json.dumps({'case':'replan_ignores_kill_switch','kill_switch':control.kill_switch_enabled(),'status':a.status,'executor_calls':len(count)}))

ws=sandbox/'empty-git'; ws.mkdir(); subprocess.run(['git','init','-q',str(ws)],check=True)
(ws/'new.py').write_text('OPENAI_API_KEY="sk-'+'x'*45+'"\n')
a=SecretsGate(ws).check(request=None,result={})
print(json.dumps({'case':'secrets_empty_git','passed':a.passed,'reason':a.reason}))
from bauer.cost_meter import report_llm_cost
from bauer.account_usage import normalize_usage
from bauer.usage_pricing import estimate_cost_usd
raw={'input_tokens':1000,'output_tokens':500}
print(json.dumps({'case':'anthropic_raw_cost_zero','sink_cost':report_llm_cost('anthropic','claude-sonnet-4-5',raw),'normalized_cost':estimate_cost_usd('anthropic','claude-sonnet-4-5',normalize_usage(raw))}))
from bauer.core.kernel.gates.scope import ScopeGate, arquivos_alterados
from bauer.core.task.contract import TaskContract
ws=sandbox/'scope-git'; ws.mkdir(); subprocess.run(['git','init','-q',str(ws)],check=True)
(ws/'src').mkdir(); (ws/'src'/'private.py').write_text('should_be_forbidden = True\n')
contract=TaskContract.from_mapping({'scope':{'allowed':['src/'],'forbidden':['src/private.py']}})
a=ScopeGate(ws,contrato=contract).check(request=None,result={})
print(json.dumps({'case':'scope_untracked_dir_hides_forbidden_file','git_paths':arquivos_alterados(ws),'direct_violation':contract.scope.viola('src/private.py'),'passed':a.passed,'reason':a.reason}))
