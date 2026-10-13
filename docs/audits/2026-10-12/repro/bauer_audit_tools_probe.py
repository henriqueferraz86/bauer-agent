import json, os, tempfile, sys
from pathlib import Path
from unittest.mock import patch
root=Path(tempfile.mkdtemp(prefix='bauer-tools-audit-'))
os.environ.update(BAUER_HOME=str(root/'home'), BAUER_CONFIG=str(root/'missing.yaml'), BAUER_AGENTS_FILE=str(root/'missing-agents.yaml'), BAUER_TASK_BACKEND='markdown')
from bauer.tool_router import ToolRouter
from bauer.tool_registry import ToolRegistry
from bauer.tool_dedup import ToolCallDeduper, MUTATING_TOOLS
from bauer.shell_runner import ShellRunner
import bauer.plugin_hooks
bauer.plugin_hooks.hooks.ensure_plugins_loaded=lambda:None
os.chdir(root)
ws=root/'ws'; ws.mkdir()
r=ToolRouter(workspace=ws,audit_enabled=False)
print('BUILTINS',len(r.available_tools()))
print('KANBAN_SECURITY',r.tool_security('kanban_write'))
d=ToolCallDeduper(); args={'action':'create','title':'item'}
r1=r.execute({'action':'kanban_write','args':args}); d.record('kanban_write',args,r1)
print('KANBAN_MUTATING', 'kanban_write' in MUTATING_TOOLS)
print('KANBAN_REPLAY', bool(d.check('kanban_write',args)))
d2=ToolCallDeduper(); readargs={'mode':'list'}; before=r.execute({'action':'kanban_read','args':readargs}); d2.record('kanban_read',readargs,before)
r.execute({'action':'kanban_write','args':{'action':'create','title':'item2'}}); d2.record('kanban_write',{'action':'create','title':'item2'},'ok')
print('KANBAN_STALE_READ',bool(d2.check('kanban_read',readargs)))
ext=ToolRegistry.get(); ext.register('audit_extension', lambda a:'done', description='Audit extension',args={'n':'int — required'},permission='execute',risk='critical',requires_approval=True)
print('EXTERNAL_AVAILABLE','audit_extension' in r.available_tools())
print('EXTERNAL_SCHEMA','audit_extension' in [s['function']['name'] for s in r.get_tool_schemas()])
print('EXTERNAL_SECURITY',r.tool_security('audit_extension'))
print('EXTERNAL_INFO_SECURITY',r.tool_info('audit_extension')['requires_approval'])
print('EXTERNAL_EXECUTE',r.execute({'action':'audit_extension','args':{}}))
ext.clear()
schemas={s['function']['name']:s['function']['parameters'] for s in r.get_tool_schemas()}
print('SOCIAL_CHANNELS_SCHEMA',schemas['social_post']['properties']['channels']['type'])
try:r.execute({'action':'social_post','args':{'content':'demo','channels':'dummy-id'}})
except Exception as e:print('SOCIAL_SCHEMA_VALID_REJECTED',str(e))
print('KANBAN_SCHEMA_REQUIRED',schemas['kanban_write']['required'])
dryws=root/'dry';dryws.mkdir(); dry=ToolRouter(workspace=dryws,audit_enabled=False,dry_run=True)
print('DRY_MEMORY_RESULT',dry.execute({'action':'memory','args':{'action':'set','key':'x','value':'1'}}))
print('DRY_MEMORY_FILE', (dryws/'.bauer_memory.json').exists())
print('DRY_KANBAN_RESULT',dry.execute({'action':'kanban_write','args':{'action':'create','title':'created-during-dry-run'}}))
print('DRY_KANBAN_FILES',[p.name for p in dryws.iterdir()])
# Cron bypasses allowlist + enabled policy: use harmless printf, absent from ShellRunner allowlist.
runner=ShellRunner(workspace=ws)
try:runner.validate('printf cron-audit')
except Exception as e:print('SHELL_PRINTF_DENIED',type(e).__name__)
pr=ToolRouter(workspace=ws,shell_runner=runner,audit_enabled=False,policy_enabled=True,policy_root=root/'runtime')
pr.execute({'action':'cronjob','args':{'action':'create','name':'audit','command':'printf cron-audit','mode':'shell','schedule':'every 30m'}})
print('CRON_SHELL_RESULT',pr.execute({'action':'cronjob','args':{'action':'run','name':'audit'}}).replace('\n',' | '))
# Direct shell is blocked by the same policy prior to execution.
try:pr.execute({'action':'run_command','args':{'command':'printf cron-audit'}})
except Exception as e:print('POLICY_SHELL_DENIED',str(e)[:130])
print('PROBE_ROOT',root)
