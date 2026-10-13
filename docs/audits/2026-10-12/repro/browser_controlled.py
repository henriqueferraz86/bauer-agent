import asyncio,json,time
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path('/tmp/bauer-audit-20261012');OUT=ROOT/'artifacts';BASE='http://127.0.0.1:18761';results=[];events=[]
def rec(name,status,**kw):
 d={'name':name,'status':status,**kw};results.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
 (OUT/'browser-controlled.json').write_text(json.dumps({'results':results,'sse':events},ensure_ascii=False,indent=2))
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/home/henrique/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',headless=True,args=['--no-sandbox']);c=await b.new_context(viewport={'width':1440,'height':1000});await c.tracing.start(screenshots=True,snapshots=True);page=await c.new_page()
  async def response(r):
   if '/stream?' in r.url:
    try:events.append(await r.text())
    except:pass
  page.on('response',response)
  async def send(msg):
   await page.locator('textarea').fill(msg);await page.locator('textarea').press('Enter');await page.wait_for_timeout(400)
  async def state():return await page.evaluate("JSON.parse(localStorage.getItem('bauer.chatState.v1')||'{}')")
  try:
   await page.goto(BASE+'/#/chat',wait_until='networkidle');await page.locator('textarea').wait_for()
   await send('WRITE_READ_TEST: escreva e leia o arquivo de prova.')
   await page.wait_for_function("!document.querySelector('textarea').disabled",timeout=30000)
   st=await state();answer=st.get('messages',[{}])[-1].get('text','')
   f=ROOT/'controlled'/'workspace'/'controlled-output.txt'
   rec('controlled_real_router_write_read','PASS' if f.exists() and f.read_text()=='CONTROLLED_TOOL_OK' and 'CONTROLLED_TOOL_OK' in answer else 'FAIL',answer=answer,file_exists=f.exists(),file_content=f.read_text() if f.exists() else None,tool_event_count=sum(x.count('event: tool') for x in events))
   await page.screenshot(path=str(OUT/'04-controlled-write-read.png'),full_page=True)
   await send('/new');await send('FAIL_TEST')
   await page.wait_for_function("!document.querySelector('textarea').disabled",timeout=30000)
   st=await state();text=st.get('messages',[{}])[-1].get('text','')
   rec('controlled_provider_failure_safe_ui','PASS' if text and 'AUDIT_SYNTHETIC_PROVIDER_FAILURE' not in text else 'FAIL',answer=text)
   await send('/new');before=len(events);await send('SLOW_TEST')
   await page.goto(BASE+'/#/models',wait_until='domcontentloaded');await page.wait_for_timeout(10500)
   st=await state();persisted=st.get('messages',[{}])[-1].get('text','');finished=len(events)>before
   rec('navigate_during_sse','BUG' if finished and not persisted else 'PASS',transport_finished=finished,persisted_answer=persisted,session_id=st.get('sessionId'))
   await page.goto(BASE+'/#/chat');await page.locator('textarea').wait_for();await send('/new');await send('/loop LOOP_TEST valide o fluxo de parar.')
   stop=page.get_by_title('Parar o loop (a rodada corrente termina)');await stop.wait_for(timeout=15000)
   st=await state();loop=[m['loop'] for m in st.get('messages',[]) if m.get('loop')][-1];rid=loop['runId'];await stop.click()
   for _ in range(30):
    status=await page.evaluate("async id => await (await fetch('/loop/'+id)).json()",rid)
    if status.get('state') not in ['running','starting']:break
    await page.wait_for_timeout(500)
   rec('controlled_loop_stop','PASS' if status.get('state')=='stopped' else 'FAIL',data=status)
   await page.wait_for_timeout(1000);await page.screenshot(path=str(OUT/'05-controlled-loop-stop.png'),full_page=True)
   await page.reload(wait_until='networkidle');await page.locator('textarea').wait_for()
   await page.wait_for_function("JSON.parse(localStorage.getItem('bauer.chatState.v1')||'{}').messages.some(m=>m.loop && m.loop.state==='stopped')",timeout=10000)
   restored=await state()
   rec('controlled_loop_reload','PASS' if rid in json.dumps(restored) else 'FAIL',run_id=rid,loop=[m['loop'] for m in restored.get('messages',[]) if m.get('loop')][-1])
  except Exception as e:
   rec('exception','FAIL',error=str(e)[:900]);await page.screenshot(path=str(OUT/'controlled-failure.png'),full_page=True)
  finally:
   await c.tracing.stop(path=str(OUT/'browser-controlled-trace.zip'));await b.close()
asyncio.run(main())
