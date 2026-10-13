import asyncio,json,re,time
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path('/tmp/bauer-audit-20261012');OUT=ROOT/'artifacts';BASE='http://127.0.0.1:18760';rows=[];events=[]
def rec(name,status,**kw):
 d={'name':name,'status':status,**kw};rows.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
 (OUT/'browser-real-write.json').write_text(json.dumps({'results':rows,'sse':events},ensure_ascii=False,indent=2))
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/home/henrique/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',headless=True,args=['--no-sandbox']);c=await b.new_context(viewport={'width':1440,'height':1000});await c.tracing.start(screenshots=True,snapshots=True);page=await c.new_page()
  async def onresp(r):
   if '/stream?' in r.url:
    try:events.append(await r.text())
    except:pass
  page.on('response',onresp)
  async def cmd(s):await page.locator('textarea').fill(s);await page.locator('textarea').press('Enter');await page.wait_for_timeout(500)
  async def state():return await page.evaluate("JSON.parse(localStorage.getItem('bauer.chatState.v1')||'{}')")
  try:
   await page.goto(BASE+'/#/chat',wait_until='networkidle');await page.get_by_label('E-mail').fill('audit@example.test');await page.get_by_label('Senha',exact=True).fill('Audit-only-password-2026');await page.get_by_role('button',name=re.compile('Entrar')).click();await page.locator('textarea').wait_for()
   await cmd('/new');t=time.monotonic();await cmd('Use write_file para criar browser-real-output.txt com o conteúdo exato REAL_BROWSER_WRITE_OK. Depois use read_file para reler esse arquivo e confirme o conteúdo lido. Só escreva esse arquivo.')
   await page.wait_for_function("!document.querySelector('textarea').disabled",timeout=240000)
   f=ROOT/'workspace'/'browser-real-output.txt';st=await state();answer=st.get('messages',[{}])[-1].get('text','')
   rec('real_write_read_roundtrip','PASS' if f.exists() and f.read_text().strip()=='REAL_BROWSER_WRITE_OK' and 'REAL_BROWSER_WRITE_OK' in answer and any('write_file' in e and 'read_file' in e for e in events) else 'FAIL',answer=answer,file_exists=f.exists(),file_content=f.read_text() if f.exists() else None,elapsed_s=round(time.monotonic()-t,2),tool_events=sum(e.count('event: tool') for e in events))
   await page.screenshot(path=str(OUT/'06-real-write-read.png'),full_page=True)
   await page.reload(wait_until='networkidle');await page.locator('textarea').wait_for();st2=await state()
   rec('real_written_conversation_reload','PASS' if st2.get('sessionId')==st.get('sessionId') and st2.get('messages')==st.get('messages') else 'FAIL')
   # Explicit observation of HUD after model selection in the same component.
   await cmd('/model ollama qwen2.5-coder:3b');await page.wait_for_function("document.body.innerText.includes('Modelo trocado')",timeout=15000)
   active=await page.evaluate("async()=>await (await fetch('/status')).json()")
   hud=await page.locator('.hud').inner_text() if await page.locator('.hud').count() else ''
   rec('hud_after_model_switch','BUG' if active['model']=='qwen2.5-coder:3b' and 'qwen3-coder:30b' in hud else 'PASS',backend_model=active['model'],hud=hud)
   await page.screenshot(path=str(OUT/'07-model-hud-divergence.png'),full_page=True)
   await cmd('/model ollama qwen3-coder:30b')
  except Exception as e:rec('exception','FAIL',error=str(e)[:800]);await page.screenshot(path=str(OUT/'real-write-failure.png'),full_page=True)
  finally:await c.tracing.stop(path=str(OUT/'browser-real-write-trace.zip'));await b.close()
asyncio.run(main())
