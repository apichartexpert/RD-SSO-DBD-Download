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
    "browser_channel": "chrome",
    "browser_profile": "browser_profile",
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if CONFIG_PATH.exists():
        cfg.update(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {})
        if not cfg.get("output_root") or str(cfg["output_root"]).startswith("D:/เอกสารบริษัท"):
            cfg["output_root"] = DEFAULT_FOLDER
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

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start_browser(self, portals, company):
        self.stop.clear()
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
                for i, key in enumerate(portals):
                    page = ctx.pages[0] if (i == 0 and ctx.pages) else ctx.new_page()
                    try:
                        page.goto(td.PORTALS[key][1], timeout=60000)
                    except Exception as e:
                        self.message = f"เปิด {td.PORTALS[key][0]} ไม่สำเร็จ: {e}"
                self.state = "running"
                self.message = "ล็อกอินในหน้าต่างเบราว์เซอร์ แล้วกดดาวน์โหลด/เปิด PDF ตามปกติ"
                while not self.stop.is_set() and not closed["v"]:
                    try:
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
            self.message = f"เปิดเบราว์เซอร์ไม่ได้: {e}"
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


@app.post("/api/fs/native")
def api_fs_native():
    """เปิดหน้าต่างเลือกโฟลเดอร์ของระบบปฏิบัติการ"""
    d = request.get_json(force=True) or {}
    init = d.get("initial") or DEFAULT_FOLDER
    if not os.path.isdir(init):
        init = DEFAULT_FOLDER if os.path.isdir(DEFAULT_FOLDER) else str(Path.home())
    title = d.get("title") or "เลือกโฟลเดอร์"
    code = ("import tkinter as tk, tkinter.filedialog as fd, sys\n"
            "r=tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
            "r.lift(); r.focus_force(); r.update()\n"
            "p=fd.askdirectory(parent=r, initialdir=sys.argv[1], title=sys.argv[2], mustexist=False)\n"
            "sys.stdout.buffer.write((p or '').encode('utf-8'))\n")
    try:
        out = subprocess.run([sys.executable, "-c", code, init, title], capture_output=True, timeout=600)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.decode("utf-8", "replace")[-300:])
        p = out.stdout.decode("utf-8").strip()
        if p:
            Path(p).mkdir(parents=True, exist_ok=True)
        return jsonify(path=str(Path(p)) if p else "")
    except Exception as e:
        return jsonify(error=f"เปิดหน้าต่างเลือกโฟลเดอร์ไม่ได้: {e}"), 500


@app.post("/api/session/start")
def api_session_start():
    if SESSION.busy():
        return jsonify(error="กำลังทำงานอยู่"), 409
    if not CFG["companies"]:
        return jsonify(error="กรุณาเพิ่มบริษัทก่อน"), 400
    d = request.get_json(force=True)
    portals = [p for p in d.get("portals", []) if p in td.PORTALS] or list(td.PORTALS)
    SESSION.start_browser(portals, d.get("company"))
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


def main():
    url = f"http://{HOST}:{PORT}/"
    print(f"TaxDocs Web กำลังทำงานที่ {url}  (ปิดหน้าต่างนี้เพื่อหยุดโปรแกรม)")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    shutil.rmtree(STAGING, ignore_errors=True)
    app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
