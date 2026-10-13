import asyncio,json,re,time
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path('/tmp/bauer-audit-20261012');OUT=ROOT/'artifacts';BASE='http://127.0.0.1:18760'
results=[];sse=[]
def rec(name,status,**kw):
 d={'name':name,'status':status,**kw};results.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
 (OUT/'browser-real-followup.json').write_text(json.dumps({'results':results,'sse':sse},ensure_ascii=False,indent=2))
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/home/henrique/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',headless=True,args=['--no-sandbox'])
  c=await b.new_context(viewport={'width':1440,'height':1000});await c.tracing.start(screenshots=True,snapshots=True);page=await c.new_page()
  async def response(r):
   if '/stream?' in r.url:
    try:sse.append(await r.text())
    except:pass
  page.on('response',response)
  async def cmd(text):
   await page.locator('textarea').fill(text);await page.locator('textarea').press('Enter');await page.wait_for_timeout(700)
  async def state():return await page.evaluate("JSON.parse(localStorage.getItem('bauer.chatState.v1')||'{}')")
  try:
   await page.goto(BASE+'/#/chat',wait_until='networkidle')
   await page.get_by_label('E-mail').fill('audit@example.test');await page.get_by_label('Senha',exact=True).fill('Audit-only-password-2026')
   await page.get_by_role('button',name=re.compile('Entrar')).click();await page.locator('textarea').wait_for(timeout=10000);rec('login','PASS')
   await cmd('/model ollama qwen3-coder:30b')
   await page.wait_for_function("document.body.innerText.includes('Modelo trocado')",timeout=10000)
   rec('model_switch_real_native','PASS',model='qwen3-coder:30b')
   await cmd('/new');rec('slash_new','PASS' if not (await state()).get('sessionId') else 'FAIL')
   t=time.monotonic();await cmd('Use read_file para ler audit_marker.txt. Responda apenas com o conteúdo real desse arquivo.')
   await page.wait_for_function("!document.querySelector('textarea').disabled",timeout=240000)
   st=await state();answer=st.get('messages',[{}])[-1].get('text','')
   rec('real_native_read_file','PASS' if 'BAUER_BROWSER_AUDIT_20261012' in answer else 'FAIL',answer=answer,elapsed_s=round(time.monotonic()-t,2),tool_events=sum('event: tool' in x for x in sse))
   await page.screenshot(path=str(OUT/'02-real-native-read-file.png'),full_page=True)
   # Exercise autonomous transport and operator stop with an installed real model.
   await cmd('/loop Use read_file para conferir audit_marker.txt e confirme que a leitura foi concluída.')
   stop=page.get_by_title('Parar o loop (a rodada corrente termina)');await stop.wait_for(timeout=15000)
   running=await state();loop=[m.get('loop') for m in running.get('messages',[]) if m.get('loop')][-1];runid=loop['runId']
   await stop.click();rec('real_loop_stop_requested','PASS',run_id=runid)
   for _ in range(120):
    status=await page.evaluate("async id => {const r=await fetch('/loop/'+id);return await r.json()}",runid)
    if status.get('state') not in ['running','starting']:break
    await page.wait_for_timeout(1000)
   rec('real_loop_terminal','PASS' if status.get('state')=='stopped' else 'FAIL',data=status)
   await page.screenshot(path=str(OUT/'03-real-loop-stopped.png'),full_page=True)
   await page.reload(wait_until='networkidle');await page.locator('textarea').wait_for()
   restored=await state();loops=[m['loop'] for m in restored.get('messages',[]) if m.get('loop')]
   rec('loop_reload_persistence','PASS' if loops and loops[-1].get('runId')==runid else 'FAIL',loop=loops[-1] if loops else None)
   await page.goto(BASE+'/#/settings');await page.get_by_role('button',name=re.compile('Sair')).wait_for(timeout=10000)
   await page.get_by_role('button',name=re.compile('Sair')).click();await page.get_by_label('E-mail').wait_for(timeout=10000)
   retained=await state();code=await page.evaluate("async()=> (await fetch('/sessions')).status")
   rec('logout_cookie_access_revoked','PASS' if code==401 else 'FAIL',http_status=code)
   rec('logout_chat_storage_retained','BUG' if retained.get('messages') else 'PASS',message_count=len(retained.get('messages',[])),session_id=retained.get('sessionId'))
   await page.get_by_label('E-mail').fill('audit@example.test');await page.get_by_label('Senha',exact=True).fill('wrong-password');await page.get_by_role('button',name=re.compile('Entrar')).click();await page.locator('.auth-error').wait_for(timeout=10000)
   rec('wrong_password_rejected','PASS')
   await page.get_by_label('Senha',exact=True).fill('Audit-only-password-2026');await page.get_by_role('button',name=re.compile('Entrar')).click();await page.locator('.sidebar').wait_for(timeout=10000);rec('login_after_logout','PASS')
  except Exception as e:
   rec('exception','FAIL',error=str(e)[:900]);await page.screenshot(path=str(OUT/'followup-failure.png'),full_page=True)
  finally:
   await c.tracing.stop(path=str(OUT/'browser-real-followup-trace.zip'));await b.close()
asyncio.run(main())
