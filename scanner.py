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
  // ปุ่ม/ลิงก์ที่เป็นการเปิดเอกสาร
  const GOOD = /(pdf|พิมพ์|print|ดาวน์โหลด|download|ใบเสร็จ|receipt|ดูแบบ|ดูเอกสาร|เรียกดู|แสดงแบบ|view|visibility|description|e-?receipt|ใบรับ|ใบนำฝาก|pay\s?-?in|file|document)/i;
  // ไม่แตะ: ปุ่มที่เปลี่ยนข้อมูลบนเว็บ
  const BADC = /(ยื่นแบบ|ยื่นเพิ่มเติม|ยื่นภาษี|ส่งแบบ|ส่งข้อมูล|นำส่ง|ยืนยัน|ลบ|ยกเลิก|แก้ไข|บันทึก|อัปโหลด|อัพโหลด|แนบ|ออกจากระบบ|logout|log out|sign out|submit|confirm|delete|remove|upload|edit|save|cancel|สมัคร|เปลี่ยนรหัส|ลืมรหัส)/i;
  const BADP = /(ชำระ|จ่าย|\bpay\b|payment)/i, DOCW = /(ใบแจ้ง|ใบเสร็จ|ใบนำฝาก|pay\s?-?in|slip|receipt|พิมพ์|print|ดาวน์โหลด|download|pdf)/i;
  const BAD = {test: t => BADC.test(t) || (BADP.test(t) && !DOCW.test(t))};
  // ไม่ใช่เอกสารภาษี: โปรแกรม คู่มือ แอป ข่าว
  const JUNK = /(\.exe|\.msi|\.zip|\.rar|\.dmg|\.apk|โปรแกรม|program|windows|32-?bit|64-?bit|rd ?prep|installer|คู่มือ|manual|แอป|app ?store|google ?play|ประกาศ|ข่าว|news|faq|คำถาม|แบบฟอร์ม(?!.*ยื่น)|ดาวน์โหลดแบบฟอร์ม)/i;
  // แถวที่เป็นรายการยื่นแบบ/ใบเสร็จ (ต้องมีชื่อแบบ หรือคำบ่งชี้)
  const RECORD = /(ภ\.?\s?ง\.?\s?ด|ภ\.?\s?พ\.?\s?\d|ภ\.?\s?ธ\.?\s?\d|อ\.?\s?ส\.?\s?4|P\.?\s?N\.?\s?D|P\.?\s?P\.?\s?\d{2}|สปส|กท\.?\s?20|ส\.?\s?บ\.?\s?ช|บ\.?\s?อ\.?\s?จ|งบการเงิน|ใบเสร็จ|เลขที่อ้างอิง|เลขที่ใบเสร็จ|reference|เงินสมทบ|งวด|เดือนภาษี|ปีภาษี|รอบบัญชี)/i;
  const PERIOD = /(25\d\d|20\d\d)/;

  const cand = document.querySelectorAll('a[href], a[onclick], button, input[type=button], input[type=submit], input[type=image], [onclick], [role=button], mat-icon, i[class*=print], i[class*=pdf], i[class*=download], span[class*=print], img[src*=print], img[src*=pdf]');
  const seenEl = new Set();
  const out = [];
  let n = 0, skippedJunk = 0;
  for (let el of cand) {
    // ไอคอนเปล่า → ใช้ตัวที่กดได้ที่ครอบอยู่
    const clickable = el.closest('a, button, [role=button], [onclick], input') || el;
    el = clickable;
    if (seenEl.has(el) || el.closest('[data-taxdocs-skip], nav, header, footer, [role=navigation], .navbar, .menu, .sidebar')) continue;
    seenEl.add(el);
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if ((r.width === 0 && r.height === 0) || st.visibility === 'hidden' || st.display === 'none') continue;
    const img = el.querySelector('img');
    const icon = el.querySelector('mat-icon, i, svg, span[class*=icon]');
    const cls = (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className || '') + ' ' +
                (icon ? (icon.getAttribute('class') || '') + ' ' + (icon.getAttribute('data-icon') || '') + ' ' + (icon.tagName === 'MAT-ICON' ? icon.innerText : '') : '');
    const own = [el.innerText, el.value, el.title, el.getAttribute('aria-label'), el.getAttribute('mattooltip'),
                 el.getAttribute('data-original-title'), el.getAttribute('ngbtooltip'), el.alt,
                 img && (img.alt || img.title)].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
    const href = el.getAttribute('href') || '';
    const onclick = el.getAttribute('onclick') || '';
    const sig = own + ' ' + href + ' ' + onclick + ' ' + cls + ' ' + (img ? img.src : '');
    if (BAD.test(own) || BAD.test(onclick)) continue;
    const isPdfLink = /\.pdf(\?|#|$)/i.test(href);
    if (!isPdfLink && !GOOD.test(sig)) continue;
    // แถวของรายการ
    const row = el.closest('tr, [role=row], mat-row, li, .list-group-item, .card, .item, .row') || el.parentElement;
    let ctx = ((row ? row.innerText : own) || own).replace(/\s+/g, ' ').trim();
    if (JUNK.test(sig) || JUNK.test(ctx.slice(0, 300))) { skippedJunk++; continue; }
    // แถวต้องเป็นรายการยื่นแบบ/ใบเสร็จ และไม่ใช่กล่องใหญ่ที่รวมทั้งหน้า
    if (!RECORD.test(ctx) && !(isPdfLink && PERIOD.test(ctx))) continue;
    if (ctx.length > 600) continue;
    let head = '';
    const table = el.closest('table, mat-table, [role=table], [role=grid]');
    if (table) { const th = table.querySelector('thead, mat-header-row, [role=columnheader], tr'); if (th) head = (th.closest('tr, mat-header-row, [role=row]') || th).innerText.replace(/\s+/g, ' ').trim().slice(0, 200); }
    const id = prefix + (n++);
    el.setAttribute('data-taxdocs-id', id);
    let abs = '';
    try { abs = href && !/^javascript:/i.test(href) && href !== '#' ? new URL(href, location.href).href : ''; } catch (e) {}
    let label = own.slice(0, 80);
    if (!label) label = /print/i.test(cls) ? 'พิมพ์ (ไอคอน)' : /pdf/i.test(cls) ? 'PDF (ไอคอน)' : /download/i.test(cls) ? 'ดาวน์โหลด (ไอคอน)' : 'เปิดเอกสาร (ไอคอน)';
    out.push({id, label, href: abs, onclick: onclick.slice(0, 200), row: ctx.slice(0, 400), head,
              isPdf: isPdfLink, tag: el.tagName.toLowerCase()});
  }
  return {url: location.href, title: document.title, items: out, junk: skippedJunk,
          loginForm: !!document.querySelector('input[type=password]')};
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


# --- ปุ่มเมนูท้ายแถว (⋮ / … / dropdown) ในแถวที่เป็นรายการยื่นแบบ (เช่น RD e-Filing ระบบใหม่)
TOGGLES_JS = r"""
(prefix) => {
  const RECORD = /(ภ\.?\s?ง\.?\s?ด|ภ\.?\s?พ\.?\s?\d|ภ\.?\s?ธ\.?\s?\d|อ\.?\s?ส\.?\s?4|P\.?\s?N\.?\s?D|P\.?\s?P\.?\s?\d{2}|สปส|กท\.?\s?20|ส\.?\s?บ\.?\s?ช|บ\.?\s?อ\.?\s?จ|งบการเงิน)/i;
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = []; let n = 0; const seen = new Set();
  const rows = document.querySelectorAll('tr, [role=row], mat-row, .mat-mdc-row, .row-item, .list-group-item, .card');
  for (const row of rows) {
    if (!vis(row)) continue;
    const text = row.innerText.replace(/\s+/g, ' ').trim();
    if (!RECORD.test(text) || text.length > 700) continue;
    const btns = [...row.querySelectorAll('button, a, [role=button], [aria-haspopup], [data-toggle=dropdown], [data-bs-toggle=dropdown], .dropdown-toggle, [mat-icon-button], [ngbdropdowntoggle]')].filter(vis);
    for (const b of btns) {
      if (seen.has(b)) continue;
      const t = (b.innerText || '').replace(/\s+/g, '').trim();
      const cls = ((b.getAttribute('class') || '') + ' ' + [...b.querySelectorAll('*')].map(x => (x.getAttribute('class') || '') + ' ' + (x.tagName === 'MAT-ICON' ? x.innerText : '')).join(' ')).toLowerCase();
      const isMenu = b.hasAttribute('aria-haspopup') || /dropdown|more|kebab|ellipsis|dots|menu|action|vert|option/.test(cls) ||
                     /^(⋮|︙|…|\.\.\.|⋯|•••|more_vert|more_horiz)$/.test(t);
      if (!isMenu) continue;
      seen.add(b);
      const id = prefix + (n++);
      b.setAttribute('data-taxdocs-menu', id);
      // แถวรายละเอียดถัดไป (ถ้าแถวนี้กางได้)
      let extra = '';
      const nx = row.nextElementSibling;
      if (nx && vis(nx) && !RECORD.test(nx.innerText.slice(0, 40))) extra = nx.innerText.replace(/\s+/g, ' ').trim().slice(0, 300);
      let head = '';
      const table = row.closest('table, mat-table, [role=table], [role=grid]');
      if (table) { const th = table.querySelector('thead tr, mat-header-row, [role=row]'); if (th && th !== row) head = th.innerText.replace(/\s+/g, ' ').trim().slice(0, 300); }
      out.push({id, row: (text + ' ' + extra).trim().slice(0, 500), head});
    }
  }
  return out;
}
"""

MENU_ITEMS_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const sel = '[role=menuitem], [role=option], .dropdown-menu a, .dropdown-menu button, .dropdown-item, .mat-menu-item, .mat-mdc-menu-item, .cdk-overlay-container button, .cdk-overlay-container a, .cdk-overlay-container [role=button], .popover a, .popover button, .ant-dropdown-menu-item, .p-menuitem-link, ul.menu li a, .context-menu a, .context-menu button';
  const items = [...document.querySelectorAll(sel)].filter(vis);
  const out = [];
  for (const it of items) {
    const t = (it.innerText || it.getAttribute('aria-label') || it.title || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 80 && !out.includes(t)) out.push(t);
  }
  return out;
}
"""

CLICK_MENU_ITEM_JS = r"""
(text) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const sel = '[role=menuitem], [role=option], .dropdown-menu a, .dropdown-menu button, .dropdown-item, .mat-menu-item, .mat-mdc-menu-item, .cdk-overlay-container button, .cdk-overlay-container a, .cdk-overlay-container [role=button], .popover a, .popover button, .ant-dropdown-menu-item, .p-menuitem-link, ul.menu li a, .context-menu a, .context-menu button';
  const it = [...document.querySelectorAll(sel)].filter(vis).find(e => norm(e.innerText || e.getAttribute('aria-label') || e.title) === text);
  if (!it) return false;
  it.click();
  return true;
}
"""

GOOD_MENU = re.compile(r"(pdf|พิมพ์|print|ดาวน์โหลด|download|ใบเสร็จ|receipt|ดูแบบ|ดูเอกสาร|เรียกดู|แสดงแบบ|view|ใบรับ|ใบนำฝาก|ใบแจ้ง|pay\s?-?in|แบบ\s?ภ|ภ\.?\s?ง\.?\s?ด|ภ\.?\s?พ)", re.I)
BAD_CORE = re.compile(r"(ยื่นแบบ|ยื่นเพิ่มเติม|ยื่นภาษี|ส่งแบบ|ส่งข้อมูล|นำส่ง|ยืนยัน|ลบ|ยกเลิก|แก้ไข|บันทึก|อัปโหลด|อัพโหลด|แนบ|ออกจากระบบ|logout|submit|confirm|delete|remove|upload|edit|save|cancel|คำร้อง|ขอคืน)", re.I)
BAD_PAY = re.compile(r"(ชำระ|จ่าย|\bpay\b|payment)", re.I)
DOC_WORDS = re.compile(r"(ใบแจ้ง|ใบเสร็จ|ใบนำฝาก|pay\s?-?in|slip|receipt|พิมพ์|print|ดาวน์โหลด|download|pdf)", re.I)


def is_bad(text):
    """ปุ่มที่เปลี่ยนข้อมูลบนเว็บ — ไม่แสดงและไม่กด ('พิมพ์ใบแจ้งชำระ' เป็นเอกสาร ไม่ใช่การจ่ายเงิน)"""
    if BAD_CORE.search(text):
        return True
    return bool(BAD_PAY.search(text)) and not DOC_WORDS.search(text)


def _close_menu(page):
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except Exception:
        pass


def _menu_items(frame, toggle_id, page):
    """กดปุ่ม ⋮ แล้วอ่านรายการในเมนู"""
    loc = frame.locator(f'[data-taxdocs-menu="{toggle_id}"]')
    if loc.count() == 0:
        return []
    loc.first.scroll_into_view_if_needed(timeout=4000)
    loc.first.click(timeout=6000)
    page.wait_for_timeout(500)
    texts = []
    for f in [frame] + [x for x in page.frames if x is not frame]:
        try:
            texts += [t for t in f.evaluate(MENU_ITEMS_JS) if t not in texts]
        except Exception:
            pass
    _close_menu(page)
    return [t for t in texts if GOOD_MENU.search(t) and not is_bad(t)]


BLOB_SRC_JS = r"""
() => [...document.querySelectorAll('iframe, embed, object')]
  .map(e => e.src || e.data || '')
  .filter(u => /^blob:/.test(u) || /\.pdf(\?|#|$)/i.test(u) || /^data:application\/pdf/.test(u))
"""

FETCH_B64_JS = r"""
async (u) => {
  const b = await (await fetch(u)).blob();
  return await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1] || ''); r.readAsDataURL(b); });
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
        if info["guessed"]:
            period = "~" + period      # เดาจากวันที่ในแถว (เช่น วันที่ยื่น) — ระบบจะอ่านงวดจริงจาก PDF อีกครั้ง
    return {
        "form": info["form"], "agency": info["agency"], "kind": kind, "period": period,
        "match": row_matches_year(text, year_be) or (info["year"] == int(year_be)),
    }


def wait_records(page, timeout=15000):
    """รอให้ตารางรายการแสดง (เว็บแบบ SPA โหลดข้อมูลทีหลัง)"""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    try:
        page.wait_for_function(
            r"""() => /(ภ\.?\s?ง\.?\s?ด|ภ\.?\s?พ\.?\s?\d|P\.?\s?N\.?\s?D|สปส|ส\.?\s?บ\.?\s?ช|บ\.?\s?อ\.?\s?จ|ไม่พบข้อมูล|ไม่มีข้อมูล|no data)/i.test(document.body.innerText)""",
            timeout=timeout)
    except Exception:
        pass
    try:
        page.wait_for_timeout(800)
    except Exception:
        pass


def scan_pages(ctx, year_be, set_year, refs, log, pages=None):
    """อ่านหน้าในเบราว์เซอร์ (ค่าเริ่มต้น: ทุกแท็บ) คืนรายการเอกสาร; refs[id] = frame สำหรับดาวน์โหลดภายหลัง"""
    results = []
    pages = [p for p in (pages or ctx.pages) if not p.is_closed() and p.url.startswith("http")]
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
    info = {"pages": [], "junk": 0, "login": False}
    for pi, p in enumerate(pages):
        for fi, f in enumerate(p.frames):
            try:
                data = f.evaluate(SCAN_JS, f"td{stamp}_{pi}_{fi}_")
            except Exception:
                continue
            info["junk"] += data.get("junk", 0)
            info["login"] = info["login"] or data.get("loginForm", False)
            if fi == 0:
                info["pages"].append(data.get("title") or data.get("url"))
            for it in data.get("items", []):
                it["page_url"] = data.get("url", "")
                it["page_title"] = data.get("title", "")
                it.update(describe(it, year_be))
                refs[it["id"]] = f
                results.append(it)
    # เมนูท้ายแถว (⋮): กดเปิดทีละแถว อ่านรายการเอกสาร แล้วปิด
    n_menu = 0
    for pi, p in enumerate(pages):
        for fi, f in enumerate(p.frames):
            try:
                toggles = f.evaluate(TOGGLES_JS, f"tm{stamp}_{pi}_{fi}_")
            except Exception:
                continue
            for tg in toggles[:60]:
                try:
                    labels = _menu_items(f, tg["id"], p)
                except Exception:
                    _close_menu(p)
                    continue
                for li, lab in enumerate(labels):
                    it = {"id": f"{tg['id']}m{li}", "label": lab, "href": "", "onclick": "", "row": tg["row"],
                          "head": tg.get("head", ""), "isPdf": False, "tag": "menu",
                          "menu": {"toggle": tg["id"], "text": lab},
                          "page_url": p.url, "page_title": ""}
                    it.update(describe(it, year_be))
                    refs[it["id"]] = f
                    results.append(it)
                    n_menu += 1
    info["menus"] = n_menu
    # ตัดรายการซ้ำ (ลิงก์เดียวกันในแถวเดียวกัน)
    uniq, seen = [], set()
    for it in results:
        key = (it["href"] or it["id"], it["row"][:120], it["label"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    scan_pages.last_info = info
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
        if item.get("menu"):
            tog = frame.locator(f'[data-taxdocs-menu="{item["menu"]["toggle"]}"]')
            if tog.count() == 0:
                return False, "หาไม่พบในหน้าเว็บ (หน้าเปลี่ยนไปแล้ว) — กดค้นหาใหม่"
            tog.first.scroll_into_view_if_needed(timeout=5000)
            tog.first.click(timeout=8000)
            frame.page.wait_for_timeout(500)
            clicked = False
            for f in [frame] + [x for x in frame.page.frames if x is not frame]:
                try:
                    if f.evaluate(CLICK_MENU_ITEM_JS, item["menu"]["text"]):
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                _close_menu(frame.page)
                return False, "ไม่พบรายการในเมนู — กดค้นหาใหม่"
        else:
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
    # 3) PDF ที่เปิดเป็น blob: ในแท็บใหม่ หรือแสดงในกรอบ (iframe/embed) บนหน้าเดิม
    if not ok:
        targets = [(np_, np_.url) for np_ in new_pages if np_.url.startswith("blob:")]
        try:
            srcs = frame.page.evaluate(BLOB_SRC_JS)
            targets += [(frame.page, u) for u in srcs]
        except Exception:
            pass
        for pg, u in targets:
            try:
                b64 = pg.evaluate(FETCH_B64_JS, u)
                data = base64.b64decode(b64) if b64 else b""
                if data[:5] == b"%PDF-":
                    keep(data, name, u if not u.startswith("blob:") else frame.page.url, hint, meta)
                    ok = True
                    break
            except Exception:
                pass
        if ok:
            try:
                frame.page.keyboard.press("Escape")   # ปิดหน้าต่างแสดง PDF บนเว็บ (ถ้ามี)
            except Exception:
                pass
    # 4) หน้าต่างใหม่ที่เป็นหน้าพิมพ์ (HTML) → แปลงเป็น PDF
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
