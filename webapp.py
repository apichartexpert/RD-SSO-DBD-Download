#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TaxDocs Web — หน้าเว็บสำหรับดาวน์โหลดและจัดเก็บแบบภาษี / ใบเสร็จ
รันบนเครื่องตัวเองเท่านั้น: python webapp.py  แล้วเปิด http://127.0.0.1:8765
"""
import csv
import os
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

import yaml
from flask import Flask, abort, jsonify, request, send_file

import taxdocs as td

HOST, PORT = "127.0.0.1", 8765
HERE = td.HERE
CONFIG_PATH = HERE / "config.yaml"
STAGING = HERE / "_staging"
TOKEN = secrets.token_hex(16)

app = Flask(__name__)
LOCK = threading.RLock()
ITEMS = {}          # id -> item
ORDER = []          # ลำดับรายการในคิว


# ---------------------------------------------------------------- config
# โฟลเดอร์เริ่มต้น: ใช้ไดรฟ์ I:\ ถ้ามี ไม่มีก็ใช้ Documents\TaxDocs
DEFAULT_FOLDER = "I:\\" if os.path.isdir("I:\\") else str(Path.home() / "Documents" / "TaxDocs")

DEFAULT_CFG = {
    "output_root": DEFAULT_FOLDER,
    "companies": [],
    "year_format": "BE",
    "confirm_each_file": True,
    "browser_channel": "msedge",
    "browser_profile": "browser_profile",
    "portal_urls": {},
    "config_version": 2,
}

# หน้าเข้าสู่ระบบของแต่ละหน่วยงาน (แก้ได้ในหน้าเว็บ ถ้าเว็บราชการเปลี่ยนที่อยู่)
PORTAL_INFO = {
    "rd": {"name": "กรมสรรพากร", "short": "RD",
           "desc": "ภ.ง.ด.1, 3, 53, 54, 50, 51 · ภ.พ.30, 36 · ใบเสร็จรับเงิน",
           "login": "ล็อกอินด้วยบัญชี RD e-Filing / Digital ID",
           "url": "https://efiling.rd.go.th"},
    "sso": {"name": "สำนักงานประกันสังคม", "short": "SSO",
            "desc": "สปส.1-10 · กท.20ก · ใบเสร็จรับเงินสมทบ",
            "login": "ล็อกอินด้วยชื่อผู้ใช้ e-Service นายจ้าง",
            "url": "https://www.sso.go.th/eservices/esv/login.do"},
    "dbd": {"name": "กรมพัฒนาธุรกิจการค้า", "short": "DBD",
            "desc": "งบการเงิน · ส.บช.3 · บอจ.5 (รายชื่อผู้ถือหุ้น)",
            "login": "ล็อกอินด้วยชื่อผู้ใช้ DBD e-Filing",
            "url": "https://efiling.dbd.go.th"},
}


def portal_url(key):
    return (CFG.get("portal_urls") or {}).get(key) or PORTAL_INFO[key]["url"]


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        cfg.update(raw)
        cfg["config_version"] = raw.get("config_version", 1)
        if not cfg.get("output_root") or str(cfg["output_root"]).startswith("D:/เอกสารบริษัท"):
            cfg["output_root"] = DEFAULT_FOLDER
        if int(cfg.get("config_version") or 1) < 2:      # ค่าตั้งรุ่นเก่า → ใช้ Edge เป็นค่าเริ่มต้น
            cfg["browser_channel"] = "msedge"
            cfg["config_version"] = 2
    cfg["portal_urls"] = cfg.get("portal_urls") or {}
    comps = []
    for c in cfg.get("companies") or []:
        tid = "".join(ch for ch in str(c.get("tax_id", "")) if ch.isdigit())
        if tid:
            comps.append({"name": str(c.get("name") or tid), "tax_id": tid})
    cfg["companies"] = comps
    return cfg


def save_cfg(cfg):
    header = "# ตั้งค่า TaxDocs — แก้ไขได้จากหน้าเว็บ (python webapp.py)\n"
    CONFIG_PATH.write_text(header + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")


CFG = load_cfg()


def company_by_id(tid):
    for c in CFG["companies"]:
        if c["tax_id"] == tid:
            return c
    return CFG["companies"][0] if CFG["companies"] else None


# ---------------------------------------------------------------- คิวเอกสาร
def period_str(info):
    if not info["year"]:
        return ""
    return str(info["year"]) + (f"-{info['month']:02d}" if info["month"] and not info["annual"] else "")


def public(item):
    info = item["info"]
    store = td.Store(CFG)
    comp = company_by_id(item["company"])
    dest = ""
    if comp:
        try:
            dest = str(store.dest(comp, info, item["orig"]).relative_to(store.root))
        except Exception:
            pass
    return {
        "id": item["id"], "orig": item["orig"], "status": item["status"],
        "form": info["form"], "agency": info["agency"], "kind": info["kind"],
        "period": period_str(info), "guessed": info["guessed"], "annual": info["annual"],
        "company": item["company"], "dest": item.get("saved_to") or dest,
        "source": item["source"], "is_pdf": item["is_pdf"], "error": item.get("error", ""),
    }


def add_item(path, orig, source, company_hint, owned):
    path = Path(path)
    data = path.read_bytes()
    is_pdf = data[:5] == b"%PDF-"
    info = td.classify(td.pdf_text(path) if is_pdf else "", orig, source)
    comp = None
    for c in CFG["companies"]:
        if c["tax_id"] in info["digits"]:
            comp = c
            break
    comp = comp or company_by_id(company_hint)
    info.pop("digits", None)
    item = {"id": uuid.uuid4().hex[:10], "path": str(path), "orig": orig, "source": source,
            "info": info, "company": comp["tax_id"] if comp else "", "owned": owned,
            "is_pdf": is_pdf, "status": "pending", "hash": td.sha256(data), "ts": time.time()}
    if comp and item["hash"] in td.Store(CFG).hashes(comp):
        item["status"] = "duplicate"
    with LOCK:
        ITEMS[item["id"]] = item
        ORDER.append(item["id"])
    if item["status"] == "pending" and not CFG.get("confirm_each_file", True):
        save_item(item)
    elif item["status"] == "duplicate" and owned:
        path.unlink(missing_ok=True)
    return item


def save_item(item, move=None):
    comp = company_by_id(item["company"])
    if not comp:
        item["status"], item["error"] = "error", "ยังไม่ได้เพิ่มบริษัทในหน้าตั้งค่า"
        return
    store = td.Store(CFG, move=item["owned"] if move is None else move)
    try:
        dest = store.save(item["path"], comp, item["info"], item["orig"], item["source"])
        if dest is None:
            item["status"] = "duplicate"
        else:
            item["status"], item["saved_to"] = "saved", str(dest.relative_to(store.root))
        if item["owned"]:
            Path(item["path"]).unlink(missing_ok=True)
    except Exception as e:
        item["status"], item["error"] = "error", str(e)


# ---------------------------------------------------------------- เบราว์เซอร์ (Playwright)
class Session:
    def __init__(self):
        self.thread = None
        self.stop = threading.Event()
        self.state = "idle"       # idle / starting / running / sorting
        self.message = ""
        self.open_q = []          # เว็บที่ขอให้เปิดเพิ่มระหว่างเบราว์เซอร์ทำงาน
        self.company = None

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def open_portal(self, key, company):
        """เปิดเว็บของหน่วยงานในเบราว์เซอร์ที่โปรแกรมควบคุม (ไฟล์ที่ดาวน์โหลดจึงถูกดักจับได้)"""
        if self.state in ("running", "starting") and self.busy():
            self.open_q.append(key)
        else:
            self.start_browser([key], company)

    def start_browser(self, portals, company):
        self.stop.clear()
        self.open_q = []
        self.company = company
        self.thread = threading.Thread(target=self._run_browser, args=(portals, company), daemon=True)
        self.thread.start()

    def _run_browser(self, portals, company):
        self.state, self.message = "starting", "กำลังเปิดเบราว์เซอร์…"
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.state, self.message = "idle", "ยังไม่ได้ติดตั้ง Playwright — รัน setup.bat"
            return
        STAGING.mkdir(exist_ok=True)
        seen = set()

        def keep(data, name, url):
            h = td.sha256(data)
            if h in seen:
                return
            seen.add(h)
            p = STAGING / f"{uuid.uuid4().hex[:8]}_{td.safe(name)}"
            p.write_bytes(data)
            add_item(p, name, url, company, owned=True)

        def on_download(d):
            try:
                tmp = STAGING / f"_dl_{time.time_ns()}"
                d.save_as(str(tmp))
                data = tmp.read_bytes()
                tmp.unlink(missing_ok=True)
                keep(data, d.suggested_filename or "download.pdf", d.url)
            except Exception as e:
                self.message = f"ดาวน์โหลดไม่สำเร็จ: {e}"

        def on_response(r):
            try:
                ct = (r.headers.get("content-type") or "").lower()
                if "pdf" not in ct and "octet-stream" not in ct:
                    return
                data = r.body()
                if not data.startswith(b"%PDF-"):
                    return
                cd = r.headers.get("content-disposition") or ""
                m = td.re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, td.re.I)
                name = td.unquote(m.group(1)) if m else (Path(td.urlparse(r.url).path).name or "document")
                if not name.lower().endswith(".pdf"):
                    name += ".pdf"
                keep(data, name, r.url)
            except Exception:
                pass

        try:
            with sync_playwright() as pw:
                kw = dict(headless=False, accept_downloads=True, viewport=None)
                if CFG.get("browser_channel"):
                    kw["channel"] = CFG["browser_channel"]
                ctx = pw.chromium.launch_persistent_context(
                    str(HERE / CFG.get("browser_profile", "browser_profile")), **kw)
                closed = {"v": False}
                ctx.on("close", lambda *_: closed.update(v=True))
                for p in ctx.pages:
                    p.on("download", on_download)
                ctx.on("page", lambda p: p.on("download", on_download))
                ctx.on("response", on_response)
                def goto(key, first=False):
                    page = ctx.pages[0] if (first and ctx.pages) else ctx.new_page()
                    try:
                        page.bring_to_front()
                        page.goto(portal_url(key), timeout=60000)
                        self.message = f"เปิด {PORTAL_INFO[key]['name']} แล้ว — ล็อกอินในหน้าต่างเบราว์เซอร์"
                    except Exception as e:
                        self.message = f"เปิด {PORTAL_INFO[key]['name']} ไม่สำเร็จ: {e}"

                for i, key in enumerate(portals):
                    goto(key, first=(i == 0))
                self.state = "running"
                while not self.stop.is_set() and not closed["v"]:
                    try:
                        while self.open_q:
                            goto(self.open_q.pop(0))
                        if not ctx.pages:
                            break
                        ctx.pages[0].wait_for_timeout(500)
                    except Exception:
                        if closed["v"] or not ctx.pages:
                            break
                        time.sleep(0.3)
                try:
                    ctx.close()
                except Exception:
                    pass
            self.message = "ปิดเบราว์เซอร์แล้ว"
        except Exception as e:
            hint = ""
            if "msedge" in str(e) or "chrome" in str(e).lower():
                hint = " — ลองเปลี่ยนเบราว์เซอร์ในหัวข้อตัวเลือก"
            self.message = f"เปิดเบราว์เซอร์ไม่ได้: {str(e).splitlines()[0]}{hint}"
        finally:
            self.state = "idle"

    def start_sort(self, folder, move, company):
        self.thread = threading.Thread(target=self._run_sort, args=(folder, move, company), daemon=True)
        self.thread.start()

    def _run_sort(self, folder, move, company):
        self.state = "sorting"
        try:
            root = Path(CFG["output_root"])
            files = sorted(p for p in Path(folder).rglob("*")
                           if p.is_file() and p.suffix.lower() == ".pdf" and root not in p.parents)
            self.message = f"พบ {len(files)} ไฟล์ — กำลังอ่าน…"
            for i, p in enumerate(files, 1):
                it = add_item(p, p.name, f"file:{p}", company, owned=False)
                it["move"] = move
                self.message = f"อ่านแล้ว {i}/{len(files)} ไฟล์"
            self.message = f"อ่านครบ {len(files)} ไฟล์แล้ว"
        except Exception as e:
            self.message = f"จัดไฟล์ไม่สำเร็จ: {e}"
        finally:
            self.state = "idle"


SESSION = Session()


# ---------------------------------------------------------------- API
@app.before_request
def check_token():
    if request.path.startswith("/api/") and request.headers.get("X-Token") != TOKEN \
            and request.args.get("t") != TOKEN:
        abort(403)


@app.get("/")
def index():
    html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    return html.replace("__TOKEN__", TOKEN)


@app.get("/api/state")
def api_state():
    return jsonify({
        "config": {k: CFG.get(k) for k in ("output_root", "companies", "year_format", "confirm_each_file", "browser_channel")},
        "session": {"state": SESSION.state, "message": SESSION.message},
        "default_folder": DEFAULT_FOLDER,
        "portal_info": {k: dict(v, url=portal_url(k), default_url=v["url"]) for k, v in PORTAL_INFO.items()},
        "forms": [{"code": f["code"], "label": f["label"], "agency": f["agency"]} for f in td.FORMS],
        "kinds": td.KINDS, "agencies": td.AGENCIES,
        "portals": {k: v[0] for k, v in td.PORTALS.items()},
    })


@app.post("/api/config")
def api_config():
    d = request.get_json(force=True)
    for k in ("output_root", "year_format", "confirm_each_file", "browser_channel"):
        if k in d:
            CFG[k] = d[k]
    if "portal_urls" in d:
        urls = {}
        for k, v in (d["portal_urls"] or {}).items():
            v = str(v or "").strip()
            if k in PORTAL_INFO and v and v != PORTAL_INFO[k]["url"]:
                if not v.startswith(("http://", "https://")):
                    return jsonify(error="ลิงก์ต้องขึ้นต้นด้วย https://"), 400
                urls[k] = v
        CFG["portal_urls"] = urls
    if "companies" in d:
        comps = []
        for c in d["companies"]:
            tid = "".join(ch for ch in str(c.get("tax_id", "")) if ch.isdigit())
            if len(tid) != 13:
                return jsonify(error=f"เลขผู้เสียภาษีต้องมี 13 หลัก: {c.get('tax_id')}"), 400
            comps.append({"name": c.get("name") or tid, "tax_id": tid})
        CFG["companies"] = comps
    save_cfg(CFG)
    return jsonify(ok=True)


def roots():
    out = []
    if sys.platform.startswith("win"):
        out += [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    else:
        out.append("/")
    home = Path.home()
    for n in ("Desktop", "Documents", "Downloads"):
        if (home / n).exists():
            out.append(str(home / n))
    out.append(str(home))
    return out


@app.get("/api/fs/list")
def api_fs_list():
    p = Path(request.args.get("path") or Path.home()).expanduser()
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        dirs = sorted((c.name for c in p.iterdir() if c.is_dir() and not c.name.startswith((".", "$"))),
                      key=str.lower)
    except PermissionError:
        dirs = []
    return jsonify(path=str(p), parent=str(p.parent) if p.parent != p else "", dirs=dirs, roots=roots())


@app.post("/api/fs/mkdir")
def api_fs_mkdir():
    d = request.get_json(force=True)
    name = td.safe(d.get("name", "")).strip()
    if not name:
        return jsonify(error="กรุณาใส่ชื่อโฟลเดอร์"), 400
    p = Path(d["path"]) / name
    p.mkdir(parents=True, exist_ok=True)
    return jsonify(path=str(p))


PS_PICKER = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false
$owner.StartPosition = 'CenterScreen'; $owner.Size = New-Object System.Drawing.Size(1,1)
$owner.Opacity = 0
$owner.Show(); $owner.Activate()
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = $env:TD_TITLE
$d.ShowNewFolderButton = $true
if (Test-Path -LiteralPath $env:TD_INIT) { $d.SelectedPath = $env:TD_INIT }
$r = $d.ShowDialog($owner)
$owner.Close()
if ($r -eq [System.Windows.Forms.DialogResult]::OK) {
  $b = [System.Text.Encoding]::UTF8.GetBytes($d.SelectedPath)
  $o = [Console]::OpenStandardOutput(); $o.Write($b, 0, $b.Length); $o.Flush()
}
"""

TK_PICKER = ("import tkinter as tk, tkinter.filedialog as fd, sys, os\n"
             "r=tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
             "r.lift(); r.focus_force(); r.update()\n"
             "p=fd.askdirectory(parent=r, initialdir=os.environ['TD_INIT'], title=os.environ['TD_TITLE'], mustexist=False)\n"
             "sys.stdout.buffer.write((p or '').encode('utf-8'))\n")

PICKING = threading.Lock()


def native_pick(init, title):
    """เปิดหน้าต่างเลือกโฟลเดอร์ของระบบ: คืน path, "" ถ้ากดยกเลิก, None ถ้าเปิดไม่ได้"""
    env = dict(os.environ, TD_INIT=init, TD_TITLE=title)
    attempts = []
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        attempts.append(dict(args=["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
                                   "-Command", PS_PICKER], creationflags=flags))
    attempts.append(dict(args=[sys.executable, "-c", TK_PICKER]))
    errors = []
    for a in attempts:
        try:
            out = subprocess.run(a["args"], capture_output=True, timeout=900, env=env,
                                 creationflags=a.get("creationflags", 0))
            if out.returncode == 0:
                return out.stdout.decode("utf-8", "replace").strip().strip("\ufeff")
            errors.append(out.stderr.decode("utf-8", "replace").strip()[-200:])
        except Exception as e:
            errors.append(str(e))
    app.logger.warning("folder picker failed: %s", " | ".join(errors))
    return None


@app.post("/api/fs/native")
def api_fs_native():
    """เปิดหน้าต่างเลือกโฟลเดอร์ของ Windows"""
    d = request.get_json(force=True) or {}
    init = d.get("initial") or DEFAULT_FOLDER
    if not os.path.isdir(init):
        init = DEFAULT_FOLDER if os.path.isdir(DEFAULT_FOLDER) else str(Path.home())
    if not PICKING.acquire(blocking=False):
        return jsonify(error="หน้าต่างเลือกโฟลเดอร์เปิดอยู่แล้ว — ดูที่แถบงานด้านล่างจอ"), 409
    try:
        p = native_pick(init, d.get("title") or "เลือกโฟลเดอร์")
    finally:
        PICKING.release()
    if p is None:
        return jsonify(error="เปิดหน้าต่างเลือกโฟลเดอร์ของ Windows ไม่ได้ — ใช้ตัวเลือกในหน้าเว็บแทน"), 500
    if p:
        Path(p).mkdir(parents=True, exist_ok=True)
    return jsonify(path=str(Path(p)) if p else "")


@app.post("/api/fs/check")
def api_fs_check():
    """ตรวจ path ที่ผู้ใช้พิมพ์เอง"""
    p = str((request.get_json(force=True) or {}).get("path") or "").strip().strip('"')
    if not p or not Path(p).is_absolute():
        return jsonify(error="กรุณาพิมพ์ path เต็ม เช่น I:\\เอกสารภาษี"), 400
    try:
        Path(p).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return jsonify(error=f"ใช้โฟลเดอร์นี้ไม่ได้: {e}"), 400
    return jsonify(path=str(Path(p)))


@app.post("/api/session/start")
def api_session_start():
    if not CFG["companies"]:
        return jsonify(error="กรุณาเพิ่มบริษัทก่อน"), 400
    d = request.get_json(force=True) or {}
    portals = [p for p in d.get("portals", []) if p in PORTAL_INFO] or list(PORTAL_INFO)
    if SESSION.state == "running" and SESSION.busy():     # เบราว์เซอร์เปิดอยู่แล้ว → เปิดแท็บเพิ่ม
        SESSION.open_q.extend(portals)
        return jsonify(ok=True)
    if SESSION.busy():
        return jsonify(error="กำลังทำงานอยู่"), 409
    SESSION.start_browser(portals, d.get("company"))
    return jsonify(ok=True)


@app.post("/api/portal/open")
def api_portal_open():
    if not CFG["companies"]:
        return jsonify(error="กรุณาเพิ่มบริษัทก่อน"), 400
    d = request.get_json(force=True)
    key = d.get("portal")
    if key not in PORTAL_INFO:
        return jsonify(error="ไม่รู้จักเว็บนี้"), 400
    if SESSION.state == "sorting":
        return jsonify(error="กำลังจัดไฟล์อยู่ รอสักครู่"), 409
    SESSION.open_portal(key, d.get("company"))
    return jsonify(ok=True)


@app.post("/api/session/stop")
def api_session_stop():
    SESSION.stop.set()
    return jsonify(ok=True)


@app.post("/api/sort")
def api_sort():
    if SESSION.busy():
        return jsonify(error="กำลังทำงานอยู่"), 409
    if not CFG["companies"]:
        return jsonify(error="กรุณาเพิ่มบริษัทก่อน"), 400
    d = request.get_json(force=True)
    folder = Path(d.get("folder", ""))
    if not folder.is_dir():
        return jsonify(error="ไม่พบโฟลเดอร์ต้นทาง"), 400
    SESSION.start_sort(str(folder), bool(d.get("move")), d.get("company"))
    return jsonify(ok=True)


@app.get("/api/queue")
def api_queue():
    with LOCK:
        items = [public(ITEMS[i]) for i in ORDER]
    return jsonify(items=list(reversed(items)))


@app.post("/api/queue/clear")
def api_queue_clear():
    with LOCK:
        for i in list(ORDER):
            if ITEMS[i]["status"] != "pending":
                ORDER.remove(i)
                ITEMS.pop(i)
    return jsonify(ok=True)


@app.post("/api/item/<iid>")
def api_item(iid):
    item = ITEMS.get(iid) or abort(404)
    d = request.get_json(force=True)
    info = item["info"]
    if "form" in d:
        f = next((f for f in td.FORMS if f["code"] == d["form"]), None)
        if f:
            info.update(form=f["code"], agency=f["agency"], annual=f["annual"])
        else:
            info.update(form="อื่นๆ", agency=d.get("agency") or info["agency"], annual=False)
    if d.get("kind") in td.KINDS:
        info["kind"] = d["kind"]
    if "period" in d and d["period"]:
        r = td.parse_period(d["period"])
        if not r:
            return jsonify(error="รูปแบบงวดไม่ถูกต้อง (เช่น 2569-09 หรือ 2569)"), 400
        info["year"], info["month"] = r
        info["guessed"] = False
    if d.get("company"):
        item["company"] = d["company"]
    action = d.get("action")
    if action == "save" and item["status"] in ("pending", "error"):
        save_item(item, move=item.get("move") if not item["owned"] else None)
    elif action == "skip" and item["status"] in ("pending", "error", "duplicate"):
        item["status"] = "skipped"
        if item["owned"]:
            Path(item["path"]).unlink(missing_ok=True)
    return jsonify(public(item))


@app.post("/api/save-all")
def api_save_all():
    n = 0
    for i in list(ORDER):
        it = ITEMS[i]
        if it["status"] == "pending" and not it["info"]["guessed"] and it["info"]["form"] != "อื่นๆ":
            save_item(it, move=it.get("move") if not it["owned"] else None)
            n += 1
    return jsonify(saved=n)


@app.get("/api/item/<iid>/file")
def api_item_file(iid):
    item = ITEMS.get(iid) or abort(404)
    p = Path(item["path"])
    if not p.exists() and item.get("saved_to"):
        p = Path(CFG["output_root"]) / item["saved_to"]
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="application/pdf" if item["is_pdf"] else None)


@app.post("/api/open-output")
def api_open_output():
    p = Path(CFG["output_root"])
    p.mkdir(parents=True, exist_ok=True)
    td.open_file(p)
    return jsonify(ok=True)


@app.get("/api/recent")
def api_recent():
    rows = []
    root = Path(CFG["output_root"])
    for c in CFG["companies"]:
        idx = td.Store(CFG).comp_dir(c) / "index.csv"
        if idx.exists():
            with open(idx, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    r["บริษัท"] = c["name"]
                    r.pop("sha256", None)
                    rows.append(r)
    rows.sort(key=lambda r: r.get("บันทึกเมื่อ", ""), reverse=True)
    return jsonify(rows=rows[:50], root=str(root))


def port_in_use():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.5)
        return sk.connect_ex((HOST, PORT)) == 0


def main():
    url = f"http://{HOST}:{PORT}/"
    if port_in_use():
        print(f"TaxDocs เปิดอยู่แล้ว — กำลังเปิด {url}")
        webbrowser.open(url)
        return
    print(f"TaxDocs Web กำลังทำงานที่ {url}  (ปิดหน้าต่างนี้เพื่อหยุดโปรแกรม)")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    shutil.rmtree(STAGING, ignore_errors=True)
    app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
