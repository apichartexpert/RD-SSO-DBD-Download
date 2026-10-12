#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TaxDocs — ดาวน์โหลดและจัดเก็บแบบภาษี / ใบเสร็จรับเงิน อย่างเป็นระเบียบ
แหล่งเอกสาร: กรมสรรพากร (RD e-Filing), สำนักงานประกันสังคม (e-Service นายจ้าง),
             กรมพัฒนาธุรกิจการค้า (DBD e-Filing: งบการเงิน, ส.บช.3, บอจ.5)

วิธีใช้
  python taxdocs.py download [--portal rd sso dbd] [--auto]
      เปิดเบราว์เซอร์ไปยังเว็บที่เลือก -> ผู้ใช้ล็อกอินเอง -> กดดาวน์โหลด/เปิด PDF ตามปกติ
      โปรแกรมดักจับทุกไฟล์ จัดหมวด ตั้งชื่อ และบันทึกลงโฟลเดอร์ใน config.yaml
      ปิดหน้าต่างเบราว์เซอร์เมื่อเสร็จ

  python taxdocs.py sort <โฟลเดอร์> [--move] [--auto]
      จัดระเบียบไฟล์ PDF ที่ดาวน์โหลดไว้แล้ว (เช่น ในโฟลเดอร์ Downloads)

  --auto  ไม่ต้องยืนยันทีละไฟล์ (แนะนำให้ใช้หลังจากทดลองแบบยืนยันจนมั่นใจแล้ว)
"""
import argparse
import csv
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

for _s in (sys.stdout, sys.stderr, sys.stdin):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import yaml
    from pypdf import PdfReader
except ImportError:
    sys.exit("ยังไม่ได้ติดตั้งไลบรารี — รัน setup.bat (หรือ pip install -r requirements.txt) ก่อน")

HERE = Path(__file__).resolve().parent

PORTALS = {
    "rd": ("กรมสรรพากร (RD e-Filing)", "https://efiling.rd.go.th"),
    "sso": ("สำนักงานประกันสังคม (e-Service นายจ้าง)", "https://www.sso.go.th"),
    "dbd": ("กรมพัฒนาธุรกิจการค้า (DBD e-Filing)", "https://efiling.dbd.go.th"),
}

AGENCIES = {
    "rd": "01_สรรพากร",
    "sso": "02_ประกันสังคม",
    "dbd": "03_DBD_งบการเงิน_ผู้ถือหุ้น",
    "other": "99_ยังไม่จัดหมวด",
}

# ---------------------------------------------------------------- กฎการจัดหมวด
def tok(code):
    """สร้าง regex ที่ยอมให้มีช่องว่างแทรกระหว่างตัวอักษร (ข้อความจาก PDF มักมีช่องว่างแปลก ๆ)"""
    return r"\s?".join(re.escape(c) for c in code)


def F(code, agency, annual, weight, *pats, label=""):
    return {"code": code, "agency": agency, "annual": annual, "w": weight,
            "re": re.compile("|".join(pats)), "label": label or code}


# code = ชื่อโฟลเดอร์ย่อย/ชื่อในไฟล์, annual = แบบรายปี (ตั้งชื่อด้วยปีอย่างเดียว)
FORMS = [
    F("ภงด1", "rd", False, 3, tok("ภงด1") + r"(?![\dก])", tok("PND1") + r"(?![\dK])", label="ภ.ง.ด.1 (เงินเดือน)"),
    F("ภงด1ก", "rd", True, 3, tok("ภงด1ก"), tok("PND1K"), label="ภ.ง.ด.1ก (รายปี)"),
    F("ภงด2", "rd", False, 3, tok("ภงด2") + r"(?!\d)", tok("PND2") + r"(?!\d)", label="ภ.ง.ด.2"),
    F("ภงด3", "rd", False, 3, tok("ภงด3") + r"(?!\d)", tok("PND3") + r"(?!\d)", label="ภ.ง.ด.3"),
    F("ภงด53", "rd", False, 3, tok("ภงด53"), tok("PND53"), label="ภ.ง.ด.53"),
    F("ภงด54", "rd", False, 3, tok("ภงด54"), tok("PND54"), label="ภ.ง.ด.54"),
    F("ภงด50", "rd", True, 3, tok("ภงด50"), tok("PND50"), label="ภ.ง.ด.50 (ภาษีเงินได้นิติบุคคลประจำปี)"),
    F("ภงด51", "rd", True, 3, tok("ภงด51"), tok("PND51"), label="ภ.ง.ด.51 (ครึ่งปี)"),
    F("ภพ30", "rd", False, 3, tok("ภพ30"), tok("PP30"), label="ภ.พ.30 (VAT)"),
    F("ภพ36", "rd", False, 3, tok("ภพ36"), tok("PP36"), label="ภ.พ.36"),
    F("ภธ40", "rd", False, 3, tok("ภธ40"), label="ภ.ธ.40 (ภาษีธุรกิจเฉพาะ)"),
    F("อส4ข", "rd", False, 3, tok("อส4ข"), label="อ.ส.4ข (อากรแสตมป์)"),
    F("สปส1-10", "sso", False, 3, tok("สปส110"), label="สปส.1-10 (เงินสมทบรายเดือน)"),
    F("กท20ก", "sso", True, 3, tok("กท20ก"), label="กท.20ก (กองทุนเงินทดแทน)"),
    F("สบช3", "dbd", True, 3, tok("สบช3") + r"(?!\d)", label="ส.บช.3 (แบบนำส่งงบการเงิน)"),
    F("บอจ5", "dbd", True, 3, tok("บอจ5") + r"(?!\d)", label="บอจ.5 (บัญชีรายชื่อผู้ถือหุ้น)"),
    F("งบการเงิน", "dbd", True, 1, r"งบการเงิน", r"งบแสดงฐานะการเงิน", r"FINANCIAL\s?STATEMENT", label="งบการเงิน"),
]

AGENCY_TEXT = [
    ("rd", r"กรมสรรพากร|REVENUE\s?DEPARTMENT"),
    ("sso", r"ประกันสังคม|SOCIAL\s?SECURITY"),
    ("dbd", r"พัฒนาธุรกิจการค้า|BUSINESS\s?DEVELOPMENT"),
]
AGENCY_HOST = {"rd.go.th": "rd", "sso.go.th": "sso", "dbd.go.th": "dbd"}

KINDS = ["แบบยื่น", "ใบเสร็จ", "ใบแจ้งชำระ", "ใบรับ"]

MONTHS = {}
for i, names in enumerate([
    ("มกราคม", "มค", "JANUARY", "JAN"), ("กุมภาพันธ์", "กพ", "FEBRUARY", "FEB"),
    ("มีนาคม", "มีค", "MARCH", "MAR"), ("เมษายน", "เมย", "APRIL", "APR"),
    ("พฤษภาคม", "พค", "MAY"), ("มิถุนายน", "มิย", "JUNE", "JUN"),
    ("กรกฎาคม", "กค", "JULY", "JUL"), ("สิงหาคม", "สค", "AUGUST", "AUG"),
    ("กันยายน", "กย", "SEPTEMBER", "SEP"), ("ตุลาคม", "ตค", "OCTOBER", "OCT"),
    ("พฤศจิกายน", "พย", "NOVEMBER", "NOV"), ("ธันวาคม", "ธค", "DECEMBER", "DEC"),
], start=1):
    for n in names:
        MONTHS[n] = i

YEAR = r"(?<!\d)(25\d\d|20\d\d)(?!\d)"
MONTH_RE = "(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"
MY_RE = re.compile(MONTH_RE + r"\s?(?:พศ\s?|BE\s?)?" + YEAR)
NUM_RE = re.compile(r"(?<!\d)(0?[1-9]|1[0-2])\s?/\s?" + YEAR)
KW_MONTH = re.compile(r"เดือนภาษี|สำหรับเดือน|ประจำเดือน|งวดเดือน|งวดภาษี|งวดค่าจ้าง|TAX\s?MONTH")
KW_YEAR = re.compile(r"ถึงวันที่|สิ้นสุดวันที่|สิ้นสุด|ณ\s?วันที่|รอบปีบัญชี|ปีบัญชี|ปีภาษี|ประจำปี")


def normalize(s):
    s = (s or "").upper().replace(".", "").replace("-", "").replace("_", " ")
    return re.sub(r"\s+", " ", s)


def be(y):
    y = int(y)
    return y + 543 if y < 2400 else y


def month_years(s):
    hits = [(m.start(), be(m.group(2)), MONTHS[m.group(1)]) for m in MY_RE.finditer(s)]
    hits += [(m.start(), be(m.group(2)), int(m.group(1))) for m in NUM_RE.finditer(s)]
    return [(y, mo) for _, y, mo in sorted(hits)]


def detect_period(nt, nf, annual):
    """คืนค่า (ปี พ.ศ., เดือน หรือ None, เดาหรือไม่)"""
    if annual:
        for kw in KW_YEAR.finditer(nt):
            m = re.search(YEAR, nt[kw.end(): kw.end() + 40])
            if m:
                return be(m.group(1)), None, False
        m = re.search(YEAR, nf)
        if m:
            return be(m.group(1)), None, False
        years = Counter(be(y) for y in re.findall(YEAR, nt))
        if years:
            return years.most_common(1)[0][0], None, True
        return None, None, True

    for kw in KW_MONTH.finditer(nt):
        r = month_years(nt[kw.end(): kw.end() + 30])
        if r:
            return r[0][0], r[0][1], False
    m = re.search(r"(?<!\d)(25\d\d|20\d\d)\s?(0[1-9]|1[0-2])(?!\d)", nf)
    if m:
        return be(m.group(1)), int(m.group(2)), False
    r = month_years(nf) or month_years(nt)
    if r:
        (y, mo), _ = Counter(r).most_common(1)[0]
        return y, mo, True
    return None, None, True


def detect_kind(s):
    if re.search(r"ใบเสร็จรับเงิน|E\s?RECEIPT|RECEIPT", s):
        return "ใบเสร็จ"
    if re.search(r"PAY\s?IN|ใบนำฝาก|ใบแจ้งการชำระ|BILL\s?PAYMENT", s):
        return "ใบแจ้งชำระ"
    if re.search(r"ใบรับ(?!รอง)|ใบยืนยันการยื่น|ACKNOWLEDG", s):
        return "ใบรับ"
    return "แบบยื่น"


def detect_agency(s, url=""):
    host = urlparse(url).hostname or ""
    for h, a in AGENCY_HOST.items():
        if host.endswith(h):
            return a
    best = max(((len(re.findall(p, s)), a) for a, p in AGENCY_TEXT), default=(0, "other"))
    return best[1] if best[0] else "other"


def classify(text, fname, url=""):
    nt, nf = normalize(text), normalize(Path(fname).stem)
    both = nt + " " + nf
    scored = []
    for idx, f in enumerate(FORMS):
        n = len(f["re"].findall(both))
        if n:
            scored.append((n * f["w"], -idx, f))
    form = max(scored, key=lambda x: (x[0], x[1]))[2] if scored else None
    annual = form["annual"] if form else False
    year, month, guessed = detect_period(nt, nf, annual)
    digits = re.sub(r"\D", "", text or "")
    return {
        "form": form["code"] if form else "อื่นๆ",
        "agency": form["agency"] if form else detect_agency(both, url),
        "annual": annual,
        "kind": detect_kind(both),
        "year": year, "month": month, "guessed": guessed,
        "digits": digits,
    }


# ---------------------------------------------------------------- อ่าน PDF
def pdf_text(path, max_pages=3):
    try:
        r = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in r.pages[:max_pages])
    except Exception:
        return ""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def safe(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .") or "file"


def open_file(p):
    try:
        if sys.platform.startswith("win"):
            os.startfile(p)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)])
        else:
            subprocess.run(["xdg-open", str(p)])
    except Exception as e:
        print(f"  เปิดไฟล์ไม่ได้: {e}")


# ---------------------------------------------------------------- การจัดเก็บ
class Store:
    def __init__(self, cfg, move=False):
        self.root = Path(cfg["output_root"]).expanduser()
        self.ce = str(cfg.get("year_format", "BE")).upper() == "CE"
        self.move = move
        self._hashes = {}

    def comp_dir(self, company):
        return self.root / safe(f"{company['tax_id']}_{company['name']}")

    def hashes(self, company):
        key = company["tax_id"]
        if key not in self._hashes:
            hs = set()
            idx = self.comp_dir(company) / "index.csv"
            if idx.exists():
                with open(idx, encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        hs.add(row.get("sha256", ""))
            self._hashes[key] = hs
        return self._hashes[key]

    def year_str(self, y):
        if not y:
            return "ไม่ทราบปี"
        return str(y - 543 if self.ce else y)

    def dest(self, company, info, orig_name):
        ext = Path(orig_name).suffix.lower() or ".pdf"
        ystr = self.year_str(info["year"])
        if info["annual"]:
            per = ystr
        elif info["year"] and info["month"]:
            per = f"{ystr}-{info['month']:02d}"
        else:
            per = "ไม่ทราบงวด"
        if info["form"] == "อื่นๆ":
            name = f"{per}_อื่นๆ_{safe(Path(orig_name).stem)}{ext}"
        else:
            name = f"{per}_{info['form']}_{info['kind']}_{company['tax_id']}{ext}"
        return self.comp_dir(company) / ystr / AGENCIES[info["agency"]] / info["form"] / name

    def save(self, src, company, info, orig_name, source=""):
        data = Path(src).read_bytes()
        h = sha256(data)
        hs = self.hashes(company)
        if h in hs:
            print("  = ไฟล์นี้เคยบันทึกแล้ว (เนื้อหาเหมือนกัน) — ข้าม")
            return None
        dest = self.dest(company, info, orig_name)
        n = 2
        base = dest
        while dest.exists():
            dest = base.with_name(f"{base.stem}_v{n}{base.suffix}")
            n += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.move:
            shutil.move(str(src), dest)
        else:
            shutil.copy2(src, dest)
        hs.add(h)
        self._append_index(company, dest, info, orig_name, source, h)
        print(f"  ✓ บันทึกแล้ว: {dest.relative_to(self.root)}")
        return dest

    def _append_index(self, company, dest, info, orig, source, h):
        idx = self.comp_dir(company) / "index.csv"
        cols = ["บันทึกเมื่อ", "หน่วยงาน", "แบบ", "ประเภท", "ปี", "เดือน",
                "ไฟล์", "ชื่อไฟล์เดิม", "แหล่งที่มา", "sha256"]
        if not idx.exists():
            idx.parent.mkdir(parents=True, exist_ok=True)
            with open(idx, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow(cols)
        with open(idx, "a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), AGENCIES[info["agency"]],
                info["form"], info["kind"], self.year_str(info["year"]), info["month"] or "",
                str(dest.relative_to(self.comp_dir(company))), orig, source, h])


# ---------------------------------------------------------------- ยืนยันกับผู้ใช้
def parse_period(s):
    s = s.strip()
    m = re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{1,2})", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return be(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{1,2})\s*[-/]\s*(\d{4})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return be(m.group(2)), int(m.group(1))
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return be(m.group(1)), None
    return None


def choose(prompt, options):
    for i, o in enumerate(options, 1):
        print(f"    {i:2d}. {o}")
    while True:
        a = input(f"  {prompt} (เลข / Enter = ยกเลิก): ").strip()
        if not a:
            return None
        if a.isdigit() and 1 <= int(a) <= len(options):
            return int(a) - 1


def confirm(info, company, path, orig_name, store, companies):
    while True:
        per = store.year_str(info["year"]) + (f"-{info['month']:02d}" if info["month"] and not info["annual"] else "")
        print("-" * 64)
        print(f"ไฟล์เดิม : {orig_name}")
        print(f"  บริษัท   : {company['name']} ({company['tax_id']})")
        print(f"  หน่วยงาน : {AGENCIES[info['agency']]}")
        print(f"  แบบ      : {info['form']}")
        print(f"  ประเภท   : {info['kind']}")
        print(f"  งวด      : {per}" + ("   ⚠ ระบบเดา — โปรดตรวจ" if info["guessed"] else ""))
        print(f"  จะบันทึก : {store.dest(company, info, orig_name).relative_to(store.root)}")
        a = input("[Enter]=บันทึก  f=แบบ  k=ประเภท  p=งวด  c=บริษัท  o=เปิดดู  s=ข้าม : ").strip().lower()
        if a == "":
            return info, company
        if a == "s":
            return None, company
        if a == "o":
            open_file(path)
        elif a == "f":
            opts = [f["label"] for f in FORMS] + ["อื่นๆ - สรรพากร", "อื่นๆ - ประกันสังคม", "อื่นๆ - DBD", "ยังไม่จัดหมวด"]
            i = choose("เลือกแบบ", opts)
            if i is not None:
                if i < len(FORMS):
                    f = FORMS[i]
                    info.update(form=f["code"], agency=f["agency"], annual=f["annual"])
                else:
                    info.update(form="อื่นๆ", agency=["rd", "sso", "dbd", "other"][i - len(FORMS)], annual=False)
        elif a == "k":
            i = choose("เลือกประเภท", KINDS)
            if i is not None:
                info["kind"] = KINDS[i]
        elif a == "p":
            r = parse_period(input("  งวด (เช่น 2569-09, 09/2569 หรือ 2569 สำหรับแบบรายปี): "))
            if r:
                info["year"], info["month"] = r
                info["guessed"] = False
            else:
                print("  รูปแบบไม่ถูกต้อง")
        elif a == "c" and len(companies) > 1:
            i = choose("เลือกบริษัท", [f"{c['name']} ({c['tax_id']})" for c in companies])
            if i is not None:
                company = companies[i]


def process_file(path, orig_name, source, company, companies, store, auto):
    text = pdf_text(path) if Path(path).read_bytes()[:5] == b"%PDF-" else ""
    info = classify(text, orig_name, source)
    # ถ้าในเอกสารพบเลขผู้เสียภาษีของบริษัทอื่นที่อยู่ในรายชื่อ ให้ใช้บริษัทนั้น
    for c in companies:
        if c["tax_id"] and c["tax_id"] in info["digits"]:
            company = c
            break
    if auto:
        print(f"• {orig_name}")
    else:
        info, company = confirm(info, company, path, orig_name, store, companies)
        if info is None:
            print("  - ข้าม")
            return
    store.save(path, company, info, orig_name, source)


# ---------------------------------------------------------------- โหมด download
def run_download(cfg, company, companies, portals, auto):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("ยังไม่ได้ติดตั้ง Playwright — รัน setup.bat ก่อน")

    store = Store(cfg)
    staging = Path(tempfile.mkdtemp(prefix="taxdocs_"))
    pending, seen = [], set()

    def keep(data, name, url):
        h = sha256(data)
        if h in seen:
            return
        seen.add(h)
        p = staging / f"{len(seen):04d}_{safe(name)}"
        p.write_bytes(data)
        pending.append((p, name, url))
        print(f"\n[รับไฟล์] {name}")

    def on_download(d):
        try:
            tmp = staging / f"_dl_{time.time_ns()}"
            d.save_as(str(tmp))
            data = tmp.read_bytes()
            tmp.unlink(missing_ok=True)
            keep(data, d.suggested_filename or "download.pdf", d.url)
        except Exception as e:
            print(f"[ดาวน์โหลดไม่สำเร็จ] {e}")

    def on_response(r):
        try:
            ct = (r.headers.get("content-type") or "").lower()
            if "pdf" not in ct and "octet-stream" not in ct:
                return
            data = r.body()
            if not data.startswith(b"%PDF-"):
                return
            cd = r.headers.get("content-disposition") or ""
            m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
            name = unquote(m.group(1)) if m else (Path(urlparse(r.url).path).name or "document")
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            keep(data, name, r.url)
        except Exception:
            pass

    profile = HERE / cfg.get("browser_profile", "browser_profile")
    with sync_playwright() as pw:
        kw = dict(headless=False, accept_downloads=True, viewport=None)
        if cfg.get("browser_channel"):
            kw["channel"] = cfg["browser_channel"]
        ctx = pw.chromium.launch_persistent_context(str(profile), **kw)
        state = {"closed": False}
        ctx.on("close", lambda *_: state.update(closed=True))
        for p in ctx.pages:
            p.on("download", on_download)
        ctx.on("page", lambda p: p.on("download", on_download))
        ctx.on("response", on_response)

        for i, key in enumerate(portals):
            page = ctx.pages[0] if (i == 0 and ctx.pages) else ctx.new_page()
            try:
                page.goto(PORTALS[key][1], timeout=60000)
            except Exception as e:
                print(f"เปิด {PORTALS[key][0]} ไม่สำเร็จ: {e}")

        print("=" * 64)
        print(f"บริษัท: {company['name']} ({company['tax_id']})")
        print("1) ล็อกอินในหน้าต่างเบราว์เซอร์ด้วยตัวเอง (รหัสผ่าน/OTP/CAPTCHA)")
        print("2) เข้าไปที่หน้าประวัติการยื่นแบบ/ใบเสร็จ แล้วกดดาวน์โหลดหรือเปิด PDF ตามปกติ")
        print("3) ทุกไฟล์จะถูกจัดหมวดและบันทึกอัตโนมัติ — ปิดหน้าต่างเบราว์เซอร์เมื่อเสร็จ")
        print("=" * 64)

        while not state["closed"]:
            try:
                pages = ctx.pages
                if not pages:
                    break
                pages[0].wait_for_timeout(700)
            except Exception:
                if state["closed"] or not ctx.pages:
                    break
                time.sleep(0.5)
            while pending:
                p, name, url = pending.pop(0)
                process_file(p, name, url, company, companies, store, auto)
        try:
            ctx.close()
        except Exception:
            pass

    while pending:
        p, name, url = pending.pop(0)
        process_file(p, name, url, company, companies, store, auto)
    shutil.rmtree(staging, ignore_errors=True)
    print(f"\nเสร็จสิ้น — ไฟล์อยู่ที่ {store.root}")


# ---------------------------------------------------------------- โหมด sort
def run_sort(cfg, company, companies, folder, move, auto):
    store = Store(cfg, move=move)
    folder = Path(folder).expanduser()
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"
                   and store.root not in p.parents)
    if not files:
        print(f"ไม่พบไฟล์ PDF ใน {folder}")
        return
    print(f"พบ {len(files)} ไฟล์ใน {folder}")
    for p in files:
        process_file(p, p.name, f"file:{p}", company, companies, store, auto)
    print(f"\nเสร็จสิ้น — ไฟล์อยู่ที่ {store.root}")


# ---------------------------------------------------------------- main
def load_config(path):
    p = Path(path)
    if not p.is_absolute():
        p = HERE / p
    if not p.exists() and (HERE / "config.example.yaml").exists():
        shutil.copy(HERE / "config.example.yaml", p)
        sys.exit(f"สร้างไฟล์ตั้งค่า {p} แล้ว — แก้ชื่อบริษัท/โฟลเดอร์ แล้วรันใหม่ (หรือใช้ run_web.bat)")
    if not p.exists():
        sys.exit(f"ไม่พบไฟล์ตั้งค่า {p}")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    comps = [c for c in cfg.get("companies") or [] if c.get("tax_id")]
    for c in comps:
        c["tax_id"] = re.sub(r"\D", "", str(c["tax_id"]))
        c["name"] = str(c.get("name") or c["tax_id"])
    if not comps:
        sys.exit("กรุณาใส่ชื่อบริษัทและเลขประจำตัวผู้เสียภาษีใน config.yaml")
    if not cfg.get("output_root"):
        sys.exit("กรุณาระบุ output_root ใน config.yaml")
    cfg["companies"] = comps
    return cfg


def pick_company(companies):
    if len(companies) == 1:
        return companies[0]
    print("เลือกบริษัท:")
    while True:
        i = choose("บริษัท", [f"{c['name']} ({c['tax_id']})" for c in companies])
        if i is not None:
            return companies[i]


def main():
    ap = argparse.ArgumentParser(description="ดาวน์โหลดและจัดเก็บแบบภาษี/ใบเสร็จ")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="เปิดเว็บหน่วยงานและดักจับไฟล์ที่ดาวน์โหลด")
    d.add_argument("--portal", nargs="+", choices=list(PORTALS), default=list(PORTALS))
    d.add_argument("--auto", action="store_true")
    s = sub.add_parser("sort", help="จัดระเบียบไฟล์ PDF ที่มีอยู่แล้ว")
    s.add_argument("folder")
    s.add_argument("--move", action="store_true", help="ย้ายไฟล์แทนการคัดลอก")
    s.add_argument("--auto", action="store_true")
    a = ap.parse_args()

    cfg = load_config(a.config)
    auto = a.auto or not cfg.get("confirm_each_file", True)
    company = pick_company(cfg["companies"])
    if a.cmd == "download":
        run_download(cfg, company, cfg["companies"], a.portal, auto)
    else:
        run_sort(cfg, company, cfg["companies"], a.folder, a.move, auto)


if __name__ == "__main__":
    main()
