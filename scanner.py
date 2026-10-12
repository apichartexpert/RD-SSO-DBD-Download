# -*- coding: utf-8 -*-
"""
ค้นหาเอกสาร (แบบ / ใบเสร็จ) ในหน้าเว็บที่ผู้ใช้ล็อกอินแล้ว และดาวน์โหลดเฉพาะรายการที่เลือก

หลักการ (ใช้ได้กับทุกเว็บ ไม่ผูกกับหน้าตาเว็บใดเว็บหนึ่ง):
  1. อ่านทุกหน้า/ทุก frame ที่เปิดอยู่ หา "ลิงก์หรือปุ่ม" ที่เป็นการพิมพ์/ดาวน์โหลด/ดูเอกสาร
  2. อ่านข้อความในแถวของตารางที่ปุ่มนั้นอยู่ เพื่อรู้ว่าเป็นแบบอะไร งวดไหน
  3. ไม่แตะปุ่มที่อาจเปลี่ยนข้อมูลบนเว็บ (ยื่นแบบ ส่ง ยืนยัน ชำระ ลบ แก้ไข ออกจากระบบ ฯลฯ)
"""
import base64
import re
import time

import taxdocs as td

# --- JavaScript ที่รันในหน้าเว็บ: คืนรายการปุ่ม/ลิงก์เอกสารพร้อมข้อความในแถว
SCAN_JS = r"""
(prefix) => {
  const GOOD = /(pdf|พิมพ์|print|ดาวน์โหลด|download|ใบเสร็จ|receipt|ดูแบบ|ดูเอกสาร|เรียกดู|แสดงแบบ|view|ส\.?\s?บ\.?\s?ช|บ\.?\s?อ\.?\s?จ|e-?receipt|ภ\.?\s?ง\.?\s?ด|ภ\.?\s?พ\.?|สปส|ใบรับ|ใบนำฝาก|pay\s?-?in)/i;
  const BAD  = /(ยื่นแบบ|ยื่นเพิ่มเติม|ยื่นภาษี|ส่งแบบ|ส่งข้อมูล|นำส่ง|ยืนยัน|ชำระ|จ่าย|ลบ|ยกเลิก|แก้ไข|บันทึก|อัปโหลด|อัพโหลด|แนบ|ออกจากระบบ|logout|log out|sign out|submit|confirm|delete|remove|pay\b|payment|upload|edit|save|cancel|สมัคร|เปลี่ยนรหัส|ลืมรหัส)/i;
  const out = [];
  const els = document.querySelectorAll('a[href], a[onclick], button, input[type=button], input[type=submit], input[type=image], [onclick], [role=button]');
  let n = 0;
  for (const el of els) {
    if (el.closest('[data-taxdocs-skip]')) continue;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if ((r.width === 0 && r.height === 0) || st.visibility === 'hidden' || st.display === 'none') continue;
    const img = el.querySelector('img');
    const own = [el.innerText, el.value, el.title, el.getAttribute('aria-label'), el.alt,
                 img && (img.alt || img.title || img.src)].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
    const href = el.getAttribute('href') || '';
    const onclick = el.getAttribute('onclick') || '';
    const sig = own + ' ' + href + ' ' + onclick;
    if (BAD.test(own) || BAD.test(onclick)) continue;
    const isPdfLink = /\.pdf(\?|#|$)/i.test(href);
    if (!isPdfLink && !GOOD.test(sig)) continue;
    // ข้อความของแถว (ตาราง / รายการ / การ์ด)
    const row = el.closest('tr, li, [role=row], .row, .card, .item, .list-group-item') || el.parentElement;
    let ctx = (row ? row.innerText : own) || own;
    ctx = ctx.replace(/\s+/g, ' ').trim().slice(0, 400);
    // หัวตาราง (ช่วยบอกชื่อคอลัมน์ เช่น เดือนภาษี / ประเภทแบบ)
    let head = '';
    const table = el.closest('table');
    if (table) { const th = table.querySelector('thead, tr'); if (th) head = th.innerText.replace(/\s+/g, ' ').trim().slice(0, 200); }
    const id = prefix + (n++);
    el.setAttribute('data-taxdocs-id', id);
    let abs = '';
    try { abs = href && !/^javascript:/i.test(href) && href !== '#' ? new URL(href, location.href).href : ''; } catch (e) {}
    out.push({id, label: own.slice(0, 80), href: abs, onclick: onclick.slice(0, 200), row: ctx, head,
              isPdf: isPdfLink, tag: el.tagName.toLowerCase()});
  }
  return {url: location.href, title: document.title, items: out};
}
"""

# --- ตั้งปีในช่องเลือกของหน้าเว็บ (ถ้ามี) แล้วกดค้นหา
SET_YEAR_JS = r"""
(years) => {
  const changed = [];
  for (const sel of document.querySelectorAll('select')) {
    const opts = [...sel.options];
    const hit = opts.find(o => years.some(y => (o.value + ' ' + o.text).replace(/\s+/g,'').includes(y)));
    const yearish = opts.filter(o => /(25\d\d|20\d\d)/.test(o.text + o.value)).length >= 2;
    if (hit && yearish && sel.value !== hit.value) {
      sel.value = hit.value;
      sel.dispatchEvent(new Event('input', {bubbles: true}));
      sel.dispatchEvent(new Event('change', {bubbles: true}));
      changed.push(sel.name || sel.id || 'select');
    }
  }
  for (const inp of document.querySelectorAll('input[type=text], input[type=number], input:not([type])')) {
    const k = ((inp.name || '') + ' ' + (inp.id || '') + ' ' + (inp.placeholder || '')).toLowerCase();
    if (/(year|ปี)/.test(k) && !/(date|วันที่)/.test(k)) {
      const want = years.find(y => String(inp.value).length === 0 || /^\d{4}$/.test(inp.value) && (inp.value.startsWith('25') === y.startsWith('25'))) || years[0];
      inp.value = want; inp.dispatchEvent(new Event('input', {bubbles: true})); inp.dispatchEvent(new Event('change', {bubbles: true}));
      changed.push(inp.name || inp.id || 'year');
    }
  }
  let clicked = '';
  if (changed.length) {
    const btn = [...document.querySelectorAll('button, input[type=button], input[type=submit], a[role=button], a.btn')]
      .find(b => /^(ค้นหา|ค้นหาข้อมูล|แสดง|แสดงข้อมูล|ตกลง|search|find|ok|go)$/i.test(((b.innerText || b.value || '') + '').trim()));
    if (btn) { btn.click(); clicked = (btn.innerText || btn.value || '').trim(); }
  }
  return {changed, clicked};
}
"""


def years_for(year_be):
    y = int(year_be)
    return [str(y), str(y - 543)]


def row_matches_year(text, year_be):
    """True ถ้าข้อความในแถวมีปีที่เลือก (พ.ศ. หรือ ค.ศ.)"""
    for m in re.finditer(r"(?<!\d)(25\d\d|20\d\d)(?!\d)", text or ""):
        if td.be(m.group(1)) == int(year_be):
            return True
    # ปี 2 หลักแบบ 09/69 หรือ ก.ย. 69
    short = str(int(year_be))[-2:]
    return bool(re.search(r"(?:/|\s|\.)" + short + r"(?!\d)", text or "")) and bool(re.search(td.MONTH_RE, td.normalize(text or "")))


def describe(item, year_be):
    """จัดหมวดจากข้อความในแถว — ใช้แสดงในรายการให้ผู้ใช้เลือก"""
    text = f"{item['row']} {item['label']} {item.get('head', '')}"
    info = td.classify(text, item.get("href") or item["label"], item.get("page_url", ""))
    # ประเภท: ดูจากชื่อปุ่มก่อน (ปุ่ม "ใบเสร็จ" ในแถวแบบยื่น คือใบเสร็จ)
    own = td.normalize(item["label"] + " " + (item.get("href") or "").rsplit("/", 1)[-1])
    if re.search(r"ใบเสร็จ|RECEIPT", own):
        kind = "ใบเสร็จ"
    elif re.search(r"PAY\s?IN|ใบนำฝาก|ใบแจ้ง", own):
        kind = "ใบแจ้งชำระ"
    elif re.search(r"ใบรับ(?!รอง)|ACKNOWLEDG", own):
        kind = "ใบรับ"
    else:
        kind = "แบบยื่น"
    period = ""
    if info["year"]:
        period = str(info["year"]) + (f"-{info['month']:02d}" if info["month"] and not info["annual"] else "")
    return {
        "form": info["form"], "agency": info["agency"], "kind": kind, "period": period,
        "match": row_matches_year(text, year_be) or (info["year"] == int(year_be)),
    }


def scan_pages(ctx, year_be, set_year, refs, log):
    """อ่านทุกหน้าในเบราว์เซอร์ คืนรายการเอกสาร; refs[id] = frame สำหรับดาวน์โหลดภายหลัง"""
    results = []
    pages = [p for p in ctx.pages if not p.is_closed() and p.url.startswith("http")]
    if set_year:
        for p in pages:
            for f in p.frames:
                try:
                    r = f.evaluate(SET_YEAR_JS, years_for(year_be))
                    if r.get("changed"):
                        log(f"ตั้งปี {year_be} ในหน้าเว็บแล้ว" + (f" และกด '{r['clicked']}'" if r.get("clicked") else ""))
                except Exception:
                    pass
        for p in pages:
            try:
                p.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            try:
                p.wait_for_timeout(800)
            except Exception:
                pass
    stamp = str(int(time.time() * 1000) % 10_000_000)
    for pi, p in enumerate(pages):
        for fi, f in enumerate(p.frames):
            try:
                data = f.evaluate(SCAN_JS, f"td{stamp}_{pi}_{fi}_")
            except Exception:
                continue
            for it in data.get("items", []):
                it["page_url"] = data.get("url", "")
                it["page_title"] = data.get("title", "")
                it.update(describe(it, year_be))
                refs[it["id"]] = f
                results.append(it)
    # ตัดรายการซ้ำ (ลิงก์เดียวกันในแถวเดียวกัน)
    uniq, seen = [], set()
    for it in results:
        key = (it["href"] or it["id"], it["row"][:120], it["label"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq


def fetch(ctx, frame, item, keep, counter, log):
    """ดาวน์โหลดรายการเดียว คืน (สำเร็จ?, ข้อความ)"""
    hint = item["row"]
    meta = {"kind": item["kind"], "form": item["form"], "period": item["period"]}
    name = td.safe((item["label"] or "document")[:40]) + ".pdf"
    base = td.unquote((item.get("href") or "").split("?")[0].rsplit("/", 1)[-1])
    if base.lower().endswith(".pdf"):
        name = base
    # 1) ลิงก์ตรง → ดึงไฟล์ด้วย session เดียวกับเบราว์เซอร์
    if item.get("href"):
        try:
            r = ctx.request.get(item["href"], timeout=45000)
            body = r.body()
            if body[:5] == b"%PDF-":
                cd = r.headers.get("content-disposition") or ""
                m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
                if m:
                    name = td.unquote(m.group(1))
                keep(body, name, item["href"], hint, meta)
                return True, "ดาวน์โหลดแล้ว"
        except Exception:
            pass
    # 2) กดปุ่มในหน้าเว็บ แล้วรอไฟล์ / หน้าต่างใหม่
    before_pages = set(ctx.pages)
    before = counter["n"]
    counter["hint"], counter["meta"] = hint, meta
    try:
        loc = frame.locator(f'[data-taxdocs-id="{item["id"]}"]')
        if loc.count() == 0:
            return False, "หาไม่พบในหน้าเว็บ (หน้าเปลี่ยนไปแล้ว) — กดค้นหาใหม่"
        loc.first.scroll_into_view_if_needed(timeout=5000)
        loc.first.click(timeout=10000)
    except Exception as e:
        counter["hint"], counter["meta"] = "", None
        return False, f"กดปุ่มไม่ได้: {str(e).splitlines()[0][:80]}"
    deadline = time.time() + 20
    new_pages = []
    while time.time() < deadline:
        if counter["n"] > before:
            break
        new_pages = [p for p in ctx.pages if p not in before_pages]
        try:
            frame.page.wait_for_timeout(400)
        except Exception:
            time.sleep(0.4)
    ok = counter["n"] > before
    # 3) หน้าต่างใหม่ที่เป็นหน้าพิมพ์ (HTML) → แปลงเป็น PDF
    if not ok:
        for np_ in new_pages:
            try:
                np_.wait_for_load_state("load", timeout=10000)
                if not np_.url.startswith("http"):
                    continue
                cdp = ctx.new_cdp_session(np_)
                res = cdp.send("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True})
                data = base64.b64decode(res["data"])
                if data[:5] == b"%PDF-":
                    keep(data, name, np_.url, hint, meta)
                    ok = True
                    break
            except Exception as e:
                log(f"แปลงหน้าพิมพ์เป็น PDF ไม่ได้: {str(e).splitlines()[0][:80]}")
    for np_ in [p for p in ctx.pages if p not in before_pages]:
        try:
            np_.close()
        except Exception:
            pass
    counter["hint"], counter["meta"] = "", None
    return (True, "ดาวน์โหลดแล้ว") if ok else (False, "ไม่ได้รับไฟล์ — ลองกดในหน้าเว็บเอง ไฟล์จะเข้าคิวเหมือนเดิม")
