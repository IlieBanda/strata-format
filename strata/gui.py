"""A no-terminal, drag-and-drop GUI for Strata.

Run ``strata gui`` and a local web app opens in your browser. Drop a file to
turn it into a smart file; drop a newer version onto it to add to its history;
click any version to download it; one button verifies or repairs the file.

It is a tiny local server (Python standard library only) talking to a
single-page UI. Nothing leaves your machine.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import Strata
from .errors import StrataError

WORKSPACE = os.path.join(os.path.expanduser("~"), "StrataFiles")


def _hsize(n: int) -> str:
    f = float(n)
    for u in ["B", "KB", "MB", "GB"]:
        if f < 1024 or u == "GB":
            return f"{f:.0f} {u}" if u == "B" else f"{f:.1f} {u}"
        f /= 1024


def _archive_path(name: str) -> str:
    name = os.path.basename(name)
    if not name.endswith(".strata"):
        name += ".strata"
    return os.path.join(WORKSPACE, name)


def _summary(path: str) -> dict:
    s = Strata(path)
    info = s.info()
    versions = s.versions()
    return {
        "archive": os.path.basename(path),
        "name": info["name"],
        "mime": info["mime"],
        "size": info["size"],
        "size_h": _hsize(info["size"]),
        "file_size_h": _hsize(os.path.getsize(path)),
        "versions": [
            {"i": i, "id": c.id[:12], "time": c.time,
             "message": c.message or "(no message)", "size_h": _hsize(c.size)}
            for i, c in enumerate(versions)
        ],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep the console quiet
        pass

    # -- helpers --
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _query(self):
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

    # -- routes --
    def do_GET(self):
        route = urllib.parse.urlparse(self.path).path
        try:
            if route == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if route == "/api/list":
                items = []
                for fn in sorted(os.listdir(WORKSPACE)):
                    if fn.endswith(".strata"):
                        try:
                            items.append(_summary(os.path.join(WORKSPACE, fn)))
                        except Exception:
                            pass
                return self._send(200, items)
            if route == "/api/summary":
                return self._send(200, _summary(_archive_path(self._query()["archive"])))
            if route == "/api/verify":
                rep = Strata(_archive_path(self._query()["archive"])).verify()
                return self._send(200, {
                    "ok": rep.ok, "blobs": rep.blobs_checked,
                    "bad": len(rep.blobs_bad),
                    "recoverable": len(rep.versions_recoverable),
                    "broken": len(rep.versions_broken),
                })
            if route == "/api/repair":
                Strata(_archive_path(self._query()["archive"])).repair()
                return self._send(200, {"ok": True})
            if route == "/api/checkout":
                q = self._query()
                s = Strata(_archive_path(q["archive"]))
                n = int(q.get("n", -1))
                data = s.read(n)
                fname = s.info()["name"] or "download"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            self._send(404, {"error": "not found"})
        except (StrataError, KeyError, FileNotFoundError) as e:
            self._send(400, {"error": str(e)})

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        try:
            filename = urllib.parse.unquote(self.headers.get("X-Filename", "file"))
            message = urllib.parse.unquote(self.headers.get("X-Message", ""))
            data = self._read_body()
            if route == "/api/wrap":
                import mimetypes
                mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                path = _archive_path(filename)
                Strata.create(path, data, mime=mime, name=os.path.basename(filename),
                              message=message or "initial version")
                return self._send(200, _summary(path))
            if route == "/api/commit":
                archive = urllib.parse.unquote(self.headers.get("X-Archive", ""))
                path = _archive_path(archive)
                Strata(path).commit(data, message=message or "new version")
                return self._send(200, _summary(path))
            self._send(404, {"error": "not found"})
        except (StrataError, KeyError, FileNotFoundError) as e:
            self._send(400, {"error": str(e)})


def serve(port: int = 8733, open_browser: bool = True) -> None:
    os.makedirs(WORKSPACE, exist_ok=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Strata is running at {url}")
    print(f"Your smart files live in: {WORKSPACE}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strata — smart files</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel2:#1c2230; --line:#2a3343;
    --text:#e6edf3; --muted:#8b949e; --accent:#3fb950; --accent2:#58a6ff;
    --warn:#f0883e; --bad:#f85149; --radius:14px;
  }
  *{box-sizing:border-box}
  body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    background:var(--bg);color:var(--text)}
  header{padding:26px 32px 10px}
  h1{margin:0;font-size:24px;letter-spacing:.3px}
  h1 .dot{color:var(--accent)}
  .sub{color:var(--muted);margin-top:4px;font-size:14px}
  .wrap{display:grid;grid-template-columns:340px 1fr;gap:22px;padding:18px 32px 40px}
  @media(max-width:820px){.wrap{grid-template-columns:1fr}}
  .drop{border:2px dashed var(--line);border-radius:var(--radius);padding:30px 18px;
    text-align:center;color:var(--muted);transition:.15s;cursor:pointer;background:var(--panel)}
  .drop.hot{border-color:var(--accent);color:var(--text);background:var(--panel2)}
  .drop b{color:var(--text)}
  .list{margin-top:18px;display:flex;flex-direction:column;gap:8px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
    padding:12px 14px;cursor:pointer;transition:.12s}
  .card:hover{border-color:var(--accent2)}
  .card.active{border-color:var(--accent);background:var(--panel2)}
  .card .n{font-weight:600}
  .card .m{color:var(--muted);font-size:12.5px;margin-top:2px}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
    padding:22px 24px;min-height:320px}
  .empty{color:var(--muted);text-align:center;padding:60px 10px}
  .head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
  .title{font-size:19px;font-weight:650}
  .meta{color:var(--muted);font-size:13px;margin-top:3px}
  .btns{display:flex;gap:8px;flex-wrap:wrap}
  button{font:inherit;border:1px solid var(--line);background:var(--panel2);color:var(--text);
    padding:8px 13px;border-radius:9px;cursor:pointer;transition:.12s}
  button:hover{border-color:var(--accent2)}
  button.primary{background:var(--accent);color:#04210d;border-color:var(--accent);font-weight:600}
  .timeline{margin-top:22px;position:relative;padding-left:22px}
  .timeline:before{content:"";position:absolute;left:6px;top:6px;bottom:6px;width:2px;background:var(--line)}
  .v{position:relative;padding:10px 0 10px 6px}
  .v:before{content:"";position:absolute;left:-19px;top:16px;width:11px;height:11px;border-radius:50%;
    background:var(--accent2);border:2px solid var(--bg)}
  .v.latest:before{background:var(--accent)}
  .v .row{display:flex;justify-content:space-between;gap:10px;align-items:center}
  .v .msg{font-weight:550}
  .v .when{color:var(--muted);font-size:12.5px}
  .v .dl{font-size:12.5px;color:var(--accent2);cursor:pointer}
  .v .dl:hover{text-decoration:underline}
  .status{margin-top:16px;padding:11px 14px;border-radius:10px;font-size:14px;display:none}
  .status.ok{display:block;background:rgba(63,185,80,.12);border:1px solid var(--accent);color:#9ff0b0}
  .status.bad{display:block;background:rgba(248,81,73,.12);border:1px solid var(--bad);color:#ffb0ab}
  .toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:var(--panel2);
    border:1px solid var(--line);padding:10px 16px;border-radius:10px;opacity:0;transition:.2s;pointer-events:none}
  .toast.show{opacity:1}
  .hint{color:var(--muted);font-size:12.5px;margin-top:14px}
</style>
</head>
<body>
<header>
  <h1>Strata<span class="dot">.</span></h1>
  <div class="sub">Files that remember their history, check themselves, and explain what they are.</div>
</header>
<div class="wrap">
  <div>
    <div id="drop" class="drop">
      <b>Drop a file here</b><br>to make a smart file
      <div class="hint">or click to choose</div>
    </div>
    <input id="file" type="file" style="display:none">
    <div id="list" class="list"></div>
  </div>
  <div id="panel" class="panel">
    <div class="empty">Pick a smart file, or drop a new file to begin.<br>
      <span style="font-size:13px">Drop a newer version onto an open file to add it to the history.</span>
    </div>
  </div>
</div>
<div id="toast" class="toast"></div>
<script>
let current=null, archives=[];
const $=s=>document.querySelector(s);
const drop=$("#drop"), fileInput=$("#file");

function toast(t){const e=$("#toast");e.textContent=t;e.classList.add("show");
  clearTimeout(e._t);e._t=setTimeout(()=>e.classList.remove("show"),2200);}
function when(ts){return new Date(ts*1000).toLocaleString();}

async function refresh(){
  archives=await (await fetch("/api/list")).json();
  const list=$("#list");
  list.innerHTML = archives.length? "" : '<div class="hint">No smart files yet.</div>';
  for(const a of archives){
    const d=document.createElement("div");
    d.className="card"+(current===a.archive?" active":"");
    d.innerHTML=`<div class="n">${a.name}</div>
      <div class="m">${a.versions.length} version(s) · ${a.size_h} · file ${a.file_size_h}</div>`;
    d.onclick=()=>open(a.archive);
    list.appendChild(d);
  }
  if(!current && archives.length){ open(archives[0].archive); }
}

async function open(archive){
  current=archive;
  const a=await (await fetch("/api/summary?archive="+encodeURIComponent(archive))).json();
  render(a); refresh();
}

function render(a){
  const vers=[...a.versions].reverse();
  const rows=vers.map(v=>`
    <div class="v ${v.i===a.versions.length-1?'latest':''}">
      <div class="row">
        <span class="msg">${v.message}</span>
        <span class="dl" onclick="dl('${a.archive}',${v.i})">download</span>
      </div>
      <div class="when">v${v.i+1} · ${v.size_h} · ${when(v.time)} · ${v.id}</div>
    </div>`).join("");
  $("#panel").innerHTML=`
    <div class="head">
      <div>
        <div class="title">${a.name}</div>
        <div class="meta">${a.mime} · ${a.versions.length} version(s) · on disk ${a.file_size_h}</div>
      </div>
      <div class="btns">
        <button class="primary" onclick="dl('${a.archive}',-1)">Download latest</button>
        <button onclick="verify('${a.archive}')">Verify</button>
      </div>
    </div>
    <div id="status" class="status"></div>
    <div class="timeline">${rows}</div>
    <div class="hint">Drop a newer version of this file anywhere on the window to add it to the history.</div>`;
}

function dl(archive,n){window.location="/api/checkout?archive="+encodeURIComponent(archive)+"&n="+n;}

async function verify(archive){
  const r=await (await fetch("/api/verify?archive="+encodeURIComponent(archive))).json();
  const s=$("#status");
  if(r.ok){s.className="status ok";
    s.textContent=`✓ Intact. ${r.blobs} chunks checked, 0 damaged, all ${r.recoverable} version(s) recoverable.`;}
  else{s.className="status bad";
    s.innerHTML=`✗ Damage found: ${r.bad} bad chunk(s), ${r.broken} version(s) affected.
      <button onclick="repair('${archive}')" style="margin-left:8px">Repair</button>`;}
}
async function repair(archive){
  await fetch("/api/repair?archive="+encodeURIComponent(archive));
  toast("Repaired"); verify(archive);
}

async function upload(file){
  const buf=await file.arrayBuffer();
  const isCommit = current && archives.find(a=>a.archive===current);
  const url = isCommit ? "/api/commit" : "/api/wrap";
  const headers={"X-Filename":encodeURIComponent(file.name),
    "X-Message":encodeURIComponent(isCommit?"new version":"initial version")};
  if(isCommit) headers["X-Archive"]=encodeURIComponent(current);
  const r=await fetch(url,{method:"POST",headers,body:buf});
  const a=await r.json();
  if(a.error){toast("Error: "+a.error);return;}
  current=a.archive; render(a); refresh();
  toast(isCommit?"New version saved":"Smart file created");
}

drop.onclick=()=>fileInput.click();
fileInput.onchange=e=>{if(e.target.files[0])upload(e.target.files[0]);};
["dragenter","dragover"].forEach(ev=>window.addEventListener(ev,e=>{
  e.preventDefault();drop.classList.add("hot");}));
["dragleave","drop"].forEach(ev=>window.addEventListener(ev,e=>{
  e.preventDefault();if(ev!=="dragleave")return;drop.classList.remove("hot");}));
window.addEventListener("drop",e=>{e.preventDefault();drop.classList.remove("hot");
  if(e.dataTransfer.files[0])upload(e.dataTransfer.files[0]);});
refresh();
</script>
</body>
</html>"""
