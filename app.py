
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from playwright.async_api import async_playwright
import asyncio, datetime, os, zipfile

app = FastAPI()
BASE = Path(__file__).parent
STATIC = BASE / "static"
VIDEOS = BASE / "videos"
HIST = BASE / "history.txt"
STATIC.mkdir(exist_ok=True)
VIDEOS.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
app.mount("/videos", StaticFiles(directory=str(VIDEOS)), name="videos")

HTML = """
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agente con Navegador</title>
<style>body{font-family:system-ui;margin:16px;max-width:980px}.row{display:flex;gap:8px;flex-wrap:wrap}
input,button,textarea{padding:10px;border:1px solid #e5e7eb;border-radius:10px;font-size:14px}
button{background:#111;color:#fff;border:0}img{max-width:100%;border:1px solid #eee;border-radius:10px}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;background:#0b1020;color:#e6edf3;padding:12px;border-radius:10px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #e5e7eb;padding:6px;font-size:14px}</style></head>
<body>
<h2>Agente con Navegador (Render)</h2>
<div class="row">
  <input id="url" style="flex:1" value="https://example.com">
  <button onclick="run()">Ejecutar</button>
</div>
<p>Verás capturas casi en vivo y al final podrás descargar un ZIP con todas las capturas.</p>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:12px">
  <div><h3>Vista en vivo</h3><img id="live" src="/static/last.jpg?x=0" onerror="this.src='/static/last.jpg?x=0'"><p id="status"></p></div>
  <div><h3>Registro</h3><div id="log" class="mono" style="min-height:220px"></div></div>
</div>
<h3>Historial</h3>
<table id="hist"></table>
<script>
let tick=0;
function append(s){const el=document.getElementById('log');el.textContent+=s+"\\n";el.scrollTop=el.scrollHeight;}
async function run(){
  document.getElementById('log').textContent="";
  const url=document.getElementById('url').value.trim();
  const r=await fetch('/api/task?url='+encodeURIComponent(url));
  const j=await r.json();
  if(!j.ok){append('❌ '+(j.error||'Error'));return;}
  const id=j.id;
  const es=new EventSource('/api/stream?id='+id);
  es.onmessage=(e)=>{append(e.data); document.getElementById('live').src='/static/last.jpg?x='+(++tick);}
  es.onerror=()=>{append('✔️ Terminado. Descarga: '+j.zip); es.close(); loadHist();}
}
async function loadHist(){
  const r=await fetch('/api/history');const j=await r.json();
  let html='<tr><th>Fecha/Hora (UTC)</th><th>Objetivo</th><th>ZIP</th></tr>';
  for(const it of j){html+=`<tr><td>${it.ts}</td><td>${it.url}</td><td><a href="${it.zip}" target="_blank">Descargar</a></td></tr>`}
  document.getElementById('hist').innerHTML=html;
}
setInterval(()=>{document.getElementById('live').src='/static/last.jpg?x='+(++tick)},1200);loadHist();
</script></body></html>
"""

@app.get("/", response_class=HTMLResponse)
async def home(): return HTML

@app.get("/api/history")
async def history():
    if not HIST.exists(): return []
    items=[]
    for line in HIST.read_text().splitlines():
        try:
            ts,url,ziprel=line.split("|",2)
            items.append({"ts":ts,"url":url,"zip":ziprel})
        except: pass
    return list(reversed(items[-50:]))

class Bus:
    def __init__(self): self.q={}
    def create(self,i): import asyncio; self.q[i]=asyncio.Queue()
    async def send(self,i,m): q=self.q.get(i); 
    async def stream(self,i):
        q=self.q.get(i)
        if not q: yield "data: no-task\n\n"; return
        try:
            while True: yield f"data: {await q.get()}\n\n"
        except: pass
        finally: self.q.pop(i,None)
bus=Bus()

@app.get("/api/task")
async def start(url: str):
    tid=str(int(asyncio.get_event_loop().time()*1000))
    bus.create(tid)
    zipname=f"session_{tid}.zip"
    asyncio.create_task(run_session(tid,url,zipname))
    return {"ok":True,"id":tid,"zip":f"/videos/{zipname}"}

@app.get("/api/stream")
async def stream(id: str):
    async def gen():
        async for ch in bus.stream(id):
            yield ch
    return Response(gen(), media_type="text/event-stream")

async def run_session(tid: str, url: str, zipname: str):
    async def log(s): 
        q=bus.q.get(tid); 
        if q: await q.put(s)
    try:
        await log(f"Iniciando navegador…")
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = await browser.new_context(viewport={"width":1280,"height":800})
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.screenshot(path=str(STATIC/"last.jpg"), full_page=True); await log(f"Abierta: {url}")

            # Intento de aceptar cookies comunes
            for sel in ["text=Aceptar", "text=Accept", "text=Estoy de acuerdo"]:
                try:
                    await page.click(sel, timeout=2000); await log(f"Clic cookies: {sel}")
                    await page.screenshot(path=str(STATIC/"last.jpg"), full_page=True); break
                except: pass

            # Toma de ~10 capturas para “vista en vivo”
            shots=[]
            for i in range(10):
                shot = STATIC/f"shot_{tid}_{i}.jpg"
                try: await page.screenshot(path=str(shot), full_page=True); shots.append(shot)
                except: pass
                await asyncio.sleep(1.0)

            await ctx.close(); await browser.close()
            # Empaquetar capturas en ZIP descargable
            zpath = VIDEOS/zipname
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for s in shots:
                    if s.exists(): z.write(s, s.name)
            ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            HIST.write_text((HIST.read_text() if HIST.exists() else "") + f"{ts}|{url}|/videos/{zipname}\n")
            await log(f"ZIP listo: /videos/{zipname}")
            await log("Sesión terminada.")
    except Exception as e:
        await log(f"Error: {e!s}")
