import asyncio,json,time,os
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
ROOT=Path(os.environ.get('BAUER_AUDIT_ROOT','/tmp/bauer-audit-20261012'))
OUT=ROOT/'artifacts';OUT.mkdir(exist_ok=True)
BASE=os.environ.get('BAUER_AUDIT_URL','http://127.0.0.1:18760')
results=[];errors=[];responses=[];events=[]
def record(name,status,**kw):
 row={'name':name,'status':status,**kw};results.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
 (OUT/'browser-real-results.json').write_text(json.dumps({'results':results,'errors':errors,'responses':responses,'sse':events},ensure_ascii=False,indent=2))
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/home/henrique/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',headless=True,args=['--no-sandbox'])
  context=await browser.new_context(viewport={'width':1440,'height':1000})
  await context.tracing.start(screenshots=True,snapshots=True)
  page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  async def response(r):
   path=urlsplit(r.url).path
   if r.status>=400:responses.append({'path':path,'status':r.status})
   if path=='/stream':
    try:
     body=await r.text();events.append({'status':r.status,'body':body[:20000]})
    except Exception as e:events.append({'status':r.status,'error':str(e)[:150]})
  page.on('response',response)
  try:
   await page.goto(BASE,wait_until='networkidle');await page.get_by_label('E-mail').wait_for()
   record('Chromium/setup_screen','PASS',browser=browser.version)
   await page.get_by_label('E-mail').fill('audit@example.test')
   await page.get_by_label('Senha',exact=True).fill('Audit-only-password-2026')
   await page.get_by_label('Confirmar senha').fill('Audit-only-password-2026')
   await page.get_by_label('Chave de bootstrap').fill('audit-bootstrap-only')
   await page.get_by_role('button',name='Criar conta',exact=True).click()
   await page.locator('.sidebar').wait_for(timeout=15000)
   await page.screenshot(path=str(OUT/'01-authenticated.png'),full_page=True)
   record('account_setup_cookie_auth','PASS')
   for route in ['chat','agents','teams','skills','runs','approvals','runtime','autonomy','observability','audit','projects','kanban','models','gateway','logs','settings']:
    n=len(responses);await page.goto(BASE+'/#/'+route,wait_until='domcontentloaded');await page.wait_for_timeout(1100)
    text=await page.locator('body').inner_text()
    record('screen_'+route,'PASS' if await page.locator('.sidebar').count() and len(text)>40 else 'FAIL',http_errors=responses[n:],text=text[:500])
   await page.goto(BASE+'/#/chat');await page.locator('textarea').wait_for()
   async def send(name,msg,expected=None,timeout=180000):
    t=time.monotonic();ta=page.locator('textarea');await ta.fill(msg);await ta.press('Enter')
    await page.wait_for_timeout(500)
    try:
     await ta.wait_for(state='visible');await page.wait_for_function("!document.querySelector('textarea').disabled",timeout=timeout)
     text=await page.locator('body').inner_text()
     # Require response occurrence after user prompt, not simply prompt text.
     state=await page.evaluate("JSON.parse(localStorage.getItem('bauer.chatState.v1') || '{}')")
     answers=[m.get('text','') for m in state.get('messages',[]) if m.get('role')=='assistant']
     answer=answers[-1] if answers else ''
     ok=bool(answer) and (expected is None or expected in answer)
     record(name,'PASS' if ok else 'FAIL',elapsed_s=round(time.monotonic()-t,2),answer=answer[:3500],session_id=state.get('sessionId'))
     await page.screenshot(path=str(OUT/(name+'.png')),full_page=True)
     return state
    except Exception as e:
     record(name,'FAIL',elapsed_s=round(time.monotonic()-t,2),error=str(e)[:500]);await page.screenshot(path=str(OUT/(name+'-failure.png')),full_page=True)
     return {}
   state=await send('real_ollama_chat','Responda somente com a palavra AUDITORIA_OK.','AUDITORIA_OK')
   sid=state.get('sessionId');await page.reload(wait_until='networkidle')
   state2=await page.evaluate("JSON.parse(localStorage.getItem('bauer.chatState.v1') || '{}')")
   record('reload_chat_persistence','PASS' if sid and sid==state2.get('sessionId') and state2.get('messages')==state.get('messages') else 'FAIL',session_id=state2.get('sessionId'))
   await send('real_tool_read','Use a ferramenta read_file para ler audit_marker.txt na pasta de trabalho. Responda com o conteúdo exato do arquivo. Não invente o conteúdo.','BAUER_BROWSER_AUDIT_20261012')
   # Validate persisted backend session through browser same-origin authenticated API.
   backend=await page.evaluate("async () => {const r=await fetch('/sessions');return {status:r.status,data:await r.json()}}")
   record('backend_sessions','PASS' if backend['status']==200 else 'FAIL',data=backend)
   await page.goto(BASE+'/#/settings');await page.wait_for_timeout(800)
   logout=page.get_by_role('button',name='Sair',exact=True)
   if await logout.count()==0:logout=page.get_by_role('button',name='Sair da conta',exact=True)
   if await logout.count():
    await logout.click();await page.get_by_label('E-mail').wait_for(timeout=10000)
    retained=await page.evaluate("JSON.parse(localStorage.getItem('bauer.chatState.v1') || '{}')")
    record('logout_removes_cookie_access','PASS',retained_chat_messages=len(retained.get('messages',[])))
    r=await page.evaluate("async () => {const r=await fetch('/sessions');return r.status}")
    record('logged_out_api_denied','PASS' if r==401 else 'FAIL',http_status=r)
    await page.get_by_label('E-mail').fill('audit@example.test');await page.get_by_label('Senha',exact=True).fill('wrong-password')
    await page.get_by_role('button',name='Entrar',exact=True).click();await page.wait_for_timeout(500)
    record('wrong_login_rejected','PASS' if await page.locator('.auth-error').count() else 'FAIL')
    await page.get_by_label('Senha',exact=True).fill('Audit-only-password-2026');await page.get_by_role('button',name='Entrar',exact=True).click();await page.locator('.sidebar').wait_for(timeout=10000)
    record('login_again','PASS')
   else:record('logout','FAIL',error='Botão logout não localizado',text=(await page.locator('body').inner_text())[-2000:])
  except Exception as e:
   record('browser_journey_exception','FAIL',error=str(e)[:1000]);await page.screenshot(path=str(OUT/'exception.png'),full_page=True)
  finally:
   await context.tracing.stop(path=str(OUT/'browser-real-trace.zip'))
   record('javascript_errors','PASS' if not errors else 'FAIL',errors=errors)
   await browser.close()
asyncio.run(main())
