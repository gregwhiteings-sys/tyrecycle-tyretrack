"""Read-only Tailscale viewer. Binds exclusively to localhost."""
import json
from contextlib import closing
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>TyreTrack Live</title><style>body{background:#0c1725;color:#edf5ff;font:16px system-ui;margin:24px}a{color:#69cfff}button{padding:12px}#cards{display:flex;flex-wrap:wrap;gap:12px}.card{background:#1a3046;padding:18px;border-radius:12px;min-width:130px}.card b{display:block;font-size:30px}img{width:100%;max-height:65vh;object-fit:contain;background:#000}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:10px;border-bottom:1px solid #395068;text-align:left;white-space:pre-wrap}.scroll{overflow:auto}.alarm{color:#ff697b}</style></head><body><h1>TyreTrack · Live viewer</h1><p>Read only · Times in Brisbane · Supervisor responses arrive through Gmail</p><p id="connection">Connecting…</p><button onclick="document.getElementById('camera').requestFullscreen()">Full screen camera</button><p><img id="camera" alt="Waiting for camera preview"></p><div id="cards"></div><h2>No-count reports and supervisor replies</h2><p>Latest 200 incidents · Refreshes every 5 seconds</p><div class="scroll"><table><thead><tr><th>Incident</th><th>Started</th><th>Alert</th><th>Ended</th><th>Status</th><th>Reason</th><th>Supervisor</th><th>Comments</th></tr></thead><tbody id="reports"></tbody></table></div><script>
function cell(tag,value){let x=document.createElement(tag);x.textContent=value??'';return x}
async function update(){try{let r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);let d=await r.json();let age=(Date.now()/1000-d.updated);document.getElementById('connection').textContent=(age>15?'STALE — app is not updating':'Connected')+' · Updated '+d.time;let cards=document.getElementById('cards');cards.replaceChildren();for(let [name,value] of Object.entries(d.cards)){let c=cell('div',name);c.className='card';c.append(cell('b',value));cards.append(c)}let rows=document.getElementById('reports');rows.replaceChildren();for(let row of d.reports){let tr=document.createElement('tr');for(let value of row)tr.append(cell('td',value));rows.append(tr)}if(!d.reports.length){let tr=document.createElement('tr');let td=cell('td','No incidents recorded yet.');td.colSpan=8;tr.append(td);rows.append(tr)}}catch(e){document.getElementById('connection').textContent='Disconnected — '+e.message}}
function camera(){let next=new Image();next.onload=()=>{document.getElementById('camera').src=next.src;setTimeout(camera,1000)};next.onerror=()=>setTimeout(camera,3000);next.src='/camera.jpg?t='+Date.now()}update();setInterval(update,5000);camera();</script></body></html>'''

class Viewer:
    def __init__(self, db, port=8765):
        self.db = db
        self.lock = threading.Lock()
        self.state = {'updated':0, 'time':'Starting', 'cards':{}}
        self.jpeg = b''
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                path = self.path.split('?',1)[0]
                try:
                    if path == '/':
                        payload, mime = PAGE.encode(), 'text/html; charset=utf-8'
                    elif path == '/camera.jpg':
                        with owner.lock: payload = owner.jpeg
                        if not payload:
                            self.send_error(503, 'Waiting for preview'); return
                        mime = 'image/jpeg'
                    elif path == '/api/status':
                        with owner.lock: data = dict(owner.state)
                        # Read-only connection: never alters the production database.
                        with closing(sqlite3.connect(owner.db.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as con:
                            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='supervisor_no_count'").fetchone()
                            data['reports'] = con.execute("SELECT incident_id,started_brisbane,threshold_brisbane,ended_brisbane,status,reason,supervisor,comments FROM supervisor_no_count ORDER BY started_brisbane DESC LIMIT 200").fetchall() if exists else []
                        payload, mime = json.dumps(data).encode(), 'application/json'
                    else:
                        self.send_error(404); return
                    self.send_response(200)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Length', str(len(payload)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError): pass
                except Exception:
                    self.send_error(503, 'Report temporarily unavailable')
        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, cards, stamp, jpeg):
        with self.lock:
            self.state = {'updated':time.time(), 'time':stamp, 'cards':dict(cards)}
            if jpeg: self.jpeg = jpeg

    def close(self):
        self.server.shutdown()
        self.server.server_close()
