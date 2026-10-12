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


MENU_OPEN_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  return [...document.querySelectorAll('[role=menuitem], .dropdown-menu.show, .mat-menu-panel, .mat-mdc-menu-panel, .cdk-overlay-container .mat-menu-item, .cdk-overlay-container [role=menu] *, .ant-dropdown:not(.ant-dropdown-hidden)')].some(vis);
}
"""

CLICK_NEUTRAL_JS = r"""
() => { const th = document.querySelector('thead th, mat-header-cell, [role=columnheader], h1, h2'); if (th) { th.dispatchEvent(new MouseEvent('mousedown', {bubbles: true})); th.click(); return true; } return false; }
"""


def _ensure_menu_closed(frame, page, toggle_id=None):
    """ปิดเมนู ⋮ ให้แน่ใจ (บางเว็บไม่ปิดด้วย Esc) ไม่ให้บังปุ่มของแถวถัดไป"""
    _close_menu(page)
    for attempt in range(2):
        try:
            if not any(f.evaluate(MENU_OPEN_JS) for f in page.frames):
                return
        except Exception:
            return
        try:
            if attempt == 0 and toggle_id:
                frame.locator(f'[data-taxdocs-menu="{toggle_id}"]').first.click(timeout=3000, force=True)
            else:
                frame.evaluate(CLICK_NEUTRAL_JS)
            page.wait_for_timeout(300)
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
    _ensure_menu_closed(frame, page, toggle_id)
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


# --- สคริปต์ที่ใส่ในทุกหน้าตั้งแต่เปิด: จำไฟล์ที่เว็บสร้างขึ้นในหน้า (blob / data:) และการสั่งพิมพ์
INIT_JS = r"""
(() => {
  if (window.__tdHooked) return; window.__tdHooked = true;
  window.__tdBlobs = []; window.__tdPrinted = 0;
  try {
    const orig = URL.createObjectURL.bind(URL);
    URL.createObjectURL = function (obj) {
      const u = orig(obj);
      try { if (obj && typeof obj.size === 'number' && obj.size > 40) window.__tdBlobs.push({url: u, blob: obj, type: obj.type || '', t: Date.now()}); } catch (e) {}
      if (window.__tdBlobs.length > 60) window.__tdBlobs.splice(0, 20);
      return u;
    };
  } catch (e) {}
  try {
    const ow = window.open;
    window.open = function (u) {
      try { if (typeof u === 'string' && /^data:(application\/pdf|image\/)/i.test(u)) window.__tdBlobs.push({url: u, type: u.slice(5, u.indexOf(';')), t: Date.now()}); } catch (e) {}
      return ow.apply(this, arguments);
    };
  } catch (e) {}
  try { window.print = function () { window.__tdPrinted = Date.now(); }; } catch (e) {}
})();
"""

READ_BLOBS_JS = r"""
async (since) => {
  const out = [];
  for (const b of (window.__tdBlobs || [])) {
    if (b.t < since || b.taken) continue;
    try {
      if (!b.blob && /^data:/.test(b.url)) { out.push({type: b.type, b64: b.url.split(',')[1] || ''}); b.taken = true; continue; }
      const blob = b.blob || await (await fetch(b.url)).blob();
      if (blob.size > 40e6) continue;
      const b64 = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1] || ''); r.readAsDataURL(blob); });
      out.push({type: blob.type || b.type, b64}); b.taken = true;
    } catch (e) {}
  }
  return {items: out, printed: window.__tdPrinted || 0, now: Date.now()};
}
"""

# --- หน้าถัดไปของตาราง
NEXT_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const norm = s => (s || '').replace(/\s+/g, '').trim();
  document.querySelectorAll('[data-taxdocs-next]').forEach(e => e.removeAttribute('data-taxdocs-next'));
  const cands = [...document.querySelectorAll('button, a, [role=button], li, span[class*=page], div[class*=page]')].filter(vis)
                .filter(e => !e.closest('tbody tr, [role=row], mat-row, header, .navbar, .cdk-overlay-container'));
  const meta = e => [e, e.parentElement, ...e.querySelectorAll('*')].filter(Boolean)
                     .map(x => (x.getAttribute('class') || '') + ' ' + (x.getAttribute('aria-label') || '') + ' ' + (x.title || '') + ' ' + (x.tagName === 'MAT-ICON' || /icon/i.test(x.className || '') ? x.innerText : '')).join(' ');
  const disabled = e => e.disabled || e.getAttribute('aria-disabled') === 'true' || /\bdisabled\b/.test(e.className || '') ||
                        (e.closest('li') && /\bdisabled\b/.test(e.closest('li').className)) || (e.parentElement && /\bdisabled\b/.test(e.parentElement.className || ''));
  const isNext = e => {
    const t = norm(e.innerText);
    if (t.length > 20) return false;
    const a = meta(e);
    if (/(last|สุดท้าย|prev|ก่อนหน้า|first|แรก|double)/i.test(a) && !/(next|ถัดไป)/i.test(a)) return false;
    return /^(›|»|>|ถัดไป|หน้าถัดไป|next|nextpage|→|chevron_right|navigate_next|keyboard_arrow_right|>>)$/i.test(t) ||
           /(pagination-next|page-next|next-page|\bnext\b|ถัดไป|chevron-right|angle-right|arrow-right|navigate_next)/i.test(a);
  };
  // 1) ปุ่ม "ถัดไป"
  const nxt = cands.filter(isNext);
  for (const el of nxt) {
    if (disabled(el)) return {how: 'next-disabled', html: el.outerHTML.slice(0, 160)};
    const click = el.matches('a, button, [role=button]') ? el : (el.querySelector('a, button, [role=button]') || el);
    click.setAttribute('data-taxdocs-next', '1');
    return {how: 'next', html: click.outerHTML.slice(0, 160)};
  }
  // 2) เลขหน้า: หน้าปัจจุบัน (active) → กดเลขถัดไป
  const nums = cands.filter(e => /^\d{1,3}$/.test(norm(e.innerText)) && e.closest('[class*=pagin], [class*=pager], nav, ul'));
  const cur = nums.find(e => /\b(active|current|selected)\b/.test((e.className || '') + ' ' + ((e.closest('li') || {}).className || '')) || e.getAttribute('aria-current'));
  if (cur) {
    const want = parseInt(norm(cur.innerText), 10) + 1;
    const n = nums.find(e => parseInt(norm(e.innerText), 10) === want);
    if (n) { const click = n.matches('a, button') ? n : (n.querySelector('a, button') || n); click.setAttribute('data-taxdocs-next', '1'); return {how: 'number', html: click.outerHTML.slice(0, 160)}; }
    return {how: 'last-number'};
  }
  const pag = [...document.querySelectorAll('[class*=pagin], [class*=pager], mat-paginator, nav')].filter(vis).map(e => norm(e.innerText).slice(0, 80)).filter(Boolean);
  return {how: 'none', pag: pag.slice(0, 3)};
}
"""

# --- ปุ่ม "แสดงเพิ่มเติม" / โหลดเพิ่มเมื่อเลื่อนลง
MORE_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const b = [...document.querySelectorAll('button, a, [role=button]')].filter(vis)
            .find(e => /^(แสดงเพิ่มเติม|ดูเพิ่มเติม|โหลดเพิ่ม|โหลดเพิ่มเติม|แสดงทั้งหมด|ดูทั้งหมด|load more|show more|more)$/i.test((e.innerText || '').trim()));
  if (b) { b.click(); return 'more'; }
  const sc = document.scrollingElement || document.body;
  const before = sc.scrollTop; sc.scrollTop = sc.scrollHeight;
  for (const el of document.querySelectorAll('.table-responsive, .mat-table-container, cdk-virtual-scroll-viewport, [class*=scroll]')) el.scrollTop = el.scrollHeight;
  return sc.scrollTop !== before ? 'scroll' : '';
}
"""

# --- จำนวนรายการต่อหน้า: เลือกค่ามากที่สุด (ลดจำนวนหน้า)
PAGESIZE_JS = r"""
() => {
  for (const sel of document.querySelectorAll('select')) {
    const nums = [...sel.options].map(o => parseInt(o.value || o.text, 10)).filter(n => !isNaN(n));
    if (nums.length >= 2 && nums.length === sel.options.length && Math.max(...nums) <= 1000 && Math.min(...nums) >= 5) {
      const mx = String(Math.max(...nums));
      const opt = [...sel.options].find(o => (o.value || o.text).trim() === mx || parseInt(o.value || o.text, 10) === +mx);
      if (opt && sel.value !== opt.value) { sel.value = opt.value; sel.dispatchEvent(new Event('change', {bubbles: true})); return 'select:' + mx; }
    }
  }
  const ms = document.querySelector('.mat-mdc-paginator-page-size-select, .mat-paginator-page-size-select, mat-paginator mat-select');
  if (ms) { (ms.querySelector('.mat-mdc-select-trigger, .mat-select-trigger') || ms).click(); return 'matselect'; }
  return '';
}
"""

PICK_MAX_OPTION_JS = r"""
() => {
  const opts = [...document.querySelectorAll('mat-option, .mat-mdc-option, [role=option]')];
  let best = null, bv = -1;
  for (const o of opts) { const v = parseInt(o.innerText, 10); if (!isNaN(v) && v > bv) { bv = v; best = o; } }
  if (best) { best.click(); return bv; }
  return 0;
}
"""

TABLE_SIG_JS = r"""
() => { const t = document.querySelector('table tbody, mat-table, [role=table], [role=grid], table');
        return (t ? t.innerText : document.body.innerText).replace(/\s+/g, ' ').slice(0, 2000); }
"""

# --- อ่านแถวพร้อมรายละเอียด (กางแถวถ้ามีปุ่ม › แล้วหุบคืน)
ROW_DETAIL_JS = r"""
async (toggleId) => {
  const tg = document.querySelector(`[data-taxdocs-menu="${toggleId}"]`);
  if (!tg) return '';
  const row = tg.closest('tr, [role=row], mat-row, .mat-mdc-row, .row-item, .list-group-item, .card');
  if (!row) return '';
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const exp = [...row.querySelectorAll('button, [role=button], [aria-expanded], a:not([href]), a[href="#"], a[href^="javascript"]')].find(b => b !== tg && !b.contains(tg) && (
     b.getAttribute('aria-expanded') === 'false' || /^(›|>|\+|chevron_right|keyboard_arrow_right|expand_more|keyboard_arrow_down)$/i.test(norm(b.innerText)) ||
     /(expand|chevron|toggle-row)/i.test((b.getAttribute('class') || '') + ' ' + [...b.querySelectorAll('*')].map(x => x.getAttribute('class') || '').join(' '))) &&
     !/(รายละเอียด|detail|ดู|view|พิมพ์|print)/i.test(norm(b.innerText) + ' ' + (b.title || '')));
  let detail = '';
  if (exp) {
    const before = row.nextElementSibling;
    exp.click();
    await new Promise(r => setTimeout(r, 450));
    const nx = row.nextElementSibling;
    if (nx && nx !== before || (nx && /expand|detail/i.test(nx.className))) detail = norm(nx.innerText).slice(0, 400);
    const inner = norm(row.innerText);
    exp.click();
    await new Promise(r => setTimeout(r, 200));
    return (inner + ' ' + detail).slice(0, 700);
  }
  return norm(row.innerText).slice(0, 500);
}
"""

RELOCATE_JS = r"""
(args) => {
  // หาปุ่ม ⋮ ของแถวที่ขึ้นต้นด้วยข้อความเดิม (หลังเปลี่ยนหน้า/โหลดใหม่ ปุ่มเดิมถูกสร้างใหม่)
  const [sig, newId] = args;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  for (const row of document.querySelectorAll('tr, [role=row], mat-row, .mat-mdc-row, .row-item, .list-group-item, .card')) {
    if (!norm(row.innerText).startsWith(sig)) continue;
    const b = row.querySelector('[data-taxdocs-menu], [aria-haspopup], .dropdown-toggle, [class*=more], [class*=kebab], [class*=ellipsis], [class*=dots]');
    if (b) { b.setAttribute('data-taxdocs-menu', newId); return true; }
  }
  return false;
}
"""

def years_for(year_be):
    y = int(year_be)
    return [str(y), str(y - 543)]


# --- กล่องข้อความที่เว็บเปิดหลังกดเมนู (เช่น RD "เลือกรายการ": ภาพแบบ / ภาพใบเสร็จ + ปุ่มดาวน์โหลด)
MODAL_JS = r"""
(mark) => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const sel = '[role=dialog], [role=alertdialog], .modal.show, .modal.in, .modal-dialog, .modal-content, mat-dialog-container, .mat-mdc-dialog-container, .cdk-overlay-pane, .swal2-popup, .p-dialog, .ant-modal, .modal';
  const dlgs = [...document.querySelectorAll(sel)].filter(vis);
  const DL = /(ดาวน์โหลด|download|พิมพ์|print|ดูไฟล์|เปิดไฟล์|pdf)/i;
  let dlg = null;
  const fresh = b => !b.hasAttribute('data-taxdocs-pre');       // ปุ่มที่เพิ่งปรากฏหลังกดเมนู
  for (const d of dlgs.reverse()) { if ([...d.querySelectorAll('button, a, [role=button]')].some(b => vis(b) && fresh(b) && DL.test(norm(b.innerText || b.title)))) { dlg = d; break; } }
  if (!dlg) {
    // เว็บใช้กล่องของตัวเอง: หาปุ่มดาวน์โหลดใหม่ แล้วใช้กล่องที่ลอยอยู่ (fixed/absolute) ที่ครอบปุ่มนั้น
    const nb = [...document.querySelectorAll('button, a, [role=button], input[type=button]')].filter(b => vis(b) && fresh(b) && DL.test(norm(b.innerText || b.value || b.title)));
    if (!nb.length) return null;
    let a = nb[0].parentElement, best = null;
    while (a && a !== document.body) {
      const st = getComputedStyle(a);
      if (st.position === 'fixed' || (st.position === 'absolute' && parseInt(st.zIndex || '0', 10) > 10)) best = a;
      a = a.parentElement;
    }
    dlg = best || nb[0].closest('table, form, section, div') || document.body;
  }
  const btns = [...dlg.querySelectorAll('button, a, [role=button], input[type=button]')].filter(b => vis(b) && fresh(b))
               .filter(b => DL.test(norm(b.innerText || b.value || b.title || b.getAttribute('aria-label'))));
  if (!btns.length) return null;
  const items = btns.map((b, i) => {
    let row = b.closest('tr, [role=row], li, .row, .list-group-item');
    if (!row || btns.filter(x => row.contains(x)).length > 1) {      // หาแถวที่มีปุ่มนี้ปุ่มเดียว
      row = b.parentElement;
      while (row && row !== dlg && row.parentElement && btns.filter(x => row.parentElement.contains(x)).length === 1) row = row.parentElement;
    }
    b.setAttribute('data-taxdocs-mdl', mark + i);
    const t = norm(row ? row.innerText : '').replace(norm(b.innerText), '').trim();
    return {id: mark + i, text: t.slice(0, 120), btn: norm(b.innerText).slice(0, 40)};
  });
  const all = [...dlg.querySelectorAll('button, a, [role=button], input[type=button]')].filter(vis);
  const close = all.find(b => /^(ปิด|close|×|✕|x)$/i.test(norm(b.innerText)) || /close|ปิด/i.test(b.getAttribute('aria-label') || '')) ||
                [...document.querySelectorAll('[aria-label*=close i], .btn-close, .close')].filter(vis)[0];
  if (close) close.setAttribute('data-taxdocs-close', mark);
  const title = norm((dlg.querySelector('h1, h2, h3, h4, h5, .modal-title, [mat-dialog-title], .swal2-title') || {}).innerText);
  return {items, title, hasClose: !!close};
}
"""


PREMARK_JS = r"""
() => {
  // จำเฉพาะปุ่มที่ "มองเห็นอยู่" ก่อนกด (กล่องของเว็บมักซ่อนรออยู่ในหน้า — ปุ่มในกล่องที่ซ่อนต้องไม่ถูกนับว่าเก่า)
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  document.querySelectorAll('[data-taxdocs-pre]').forEach(b => b.removeAttribute('data-taxdocs-pre'));
  document.querySelectorAll('button, a, [role=button], input[type=button]').forEach(b => { if (vis(b)) b.setAttribute('data-taxdocs-pre', '1'); });
  return true;
}
"""

# ปิดกล่องที่เว็บเปิดค้าง (ปุ่ม ปิด/Close/× ที่เพิ่งปรากฏ ในกล่องที่ลอยอยู่)
CLOSE_OVERLAY_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const floating = e => { for (let a = e; a && a !== document.body; a = a.parentElement) { const st = getComputedStyle(a); if (st.position === 'fixed' || (st.position === 'absolute' && parseInt(st.zIndex || '0', 10) > 10)) return true; } return false; };
  let n = 0;
  for (const b of document.querySelectorAll('button, a, [role=button], input[type=button]')) {
    if (b.hasAttribute('data-taxdocs-pre') || !vis(b)) continue;
    const t = (b.innerText || b.value || '').replace(/\s+/g, '').trim();
    if ((/^(ปิด|close|×|✕|x)$/i.test(t) || /close|ปิด/i.test(b.getAttribute('aria-label') || '')) && floating(b)) { b.click(); n++; }
  }
  return n;
}
"""


# สิ่งที่ลอยอยู่บนหน้าจอ (กล่อง/ป๊อปอัป/แจ้งเตือน) — บันทึกไว้ช่วยวิเคราะห์
OVERLAYS_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 20 && r.height > 20 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const out = [];
  for (const e of document.querySelectorAll('body *')) {
    const st = getComputedStyle(e);
    if (!(st.position === 'fixed' || (st.position === 'absolute' && parseInt(st.zIndex || '0', 10) >= 100))) continue;
    if (!vis(e)) continue;
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length < 2) continue;
    if (out.some(o => o.t.includes(t))) continue;
    out.push({t: t.slice(0, 220), c: (e.className && e.className.baseVal === undefined ? String(e.className) : '').slice(0, 60), tag: e.tagName.toLowerCase()});
    if (out.length >= 6) break;
  }
  return out;
}
"""


def split_choices(label):
    """'พิมพ์ภาพแบบ/ภาพใบเสร็จ' → ['ภาพแบบ', 'ภาพใบเสร็จ'] (เมนูที่จะเปิดกล่องให้เลือกต่อ)"""
    core = re.sub(r"^(พิมพ์|ดาวน์โหลด|ดู|print|download)\s*", "", label.strip(), flags=re.I)
    parts = [p.strip() for p in re.split(r"\s*/\s*", core) if p.strip()]
    if len(parts) < 2 or not all(re.search(r"(ภาพ|แบบ|ใบเสร็จ|ใบรับ|ใบแจ้ง|receipt|form)", p, re.I) for p in parts):
        return []
    return parts


def _sub_match(sub, text):
    a, b = re.sub(r"\s+", "", sub or ""), re.sub(r"\s+", "", text or "")
    return bool(a) and (a in b or b in a or (len(a) >= 4 and a[:6] in b))


MONTHLY = {"ภงด1", "ภงด2", "ภงด3", "ภงด53", "ภงด54", "ภพ30", "ภพ36", "ภธ40", "อส4ข", "สปส1-10"}
PREV_YEAR = {"ภงด50", "ภงด1ก", "สบช3", "งบการเงิน", "กท20ก"}


def ym(s):
    """'2569-09' → (2569, 9)"""
    m = re.match(r"~?(\d{4})(?:-(\d{1,2}))?", s or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def estimate_period(info, text):
    """งวดภาษีโดยประมาณ: ใช้เดือนภาษีที่อ่านได้ ถ้ามีแค่วันที่ยื่น ให้เลื่อนตามประเภทแบบ"""
    y, m, guessed = info["year"], info["month"], info["guessed"]
    if not y:
        return None, None, True
    if not guessed:
        return y, (m if not info["annual"] else None), False
    form = info["form"]
    if form in MONTHLY and m:              # ยื่นเดือนถัดไป → งวด = เดือนก่อนวันที่ยื่น
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        return y, m, True
    if form in PREV_YEAR:                  # แบบรายปีของปีก่อน (ยื่นปีถัดไป)
        return y - 1, None, True
    return y, (None if info["annual"] else m), True


def in_range(y, m, rng):
    """rng = ((y1,m1),(y2,m2)); แบบรายปี (m=None) ผ่านถ้าปีอยู่ในช่วง"""
    if not rng or not y:
        return bool(not rng)
    (y1, m1), (y2, m2) = rng
    if m is None:
        return y1 <= y <= y2
    return (y1, m1) <= (y, m) <= (y2, m2)


def describe(item, rng):
    """จัดหมวดจากข้อความในแถว — ใช้แสดงในรายการให้ผู้ใช้เลือก"""
    text = f"{item['row']} {item['label']} {item.get('head', '')}"
    info = td.classify(text, item.get("href") or item["label"], item.get("page_url", ""))
    own = td.normalize(item["label"] + " " + (item.get("href") or "").rsplit("/", 1)[-1])
    if re.search(r"ใบเสร็จ|RECEIPT", own):
        kind = "ใบเสร็จ"
    elif re.search(r"PAY\s?IN|ใบนำฝาก|ใบแจ้ง", own):
        kind = "ใบแจ้งชำระ"
    elif re.search(r"ใบรับ(?!รอง)|ACKNOWLEDG", own):
        kind = "ใบรับ"
    else:
        kind = "แบบยื่น"
    y, m, approx = estimate_period(info, text)
    period = ""
    if y:
        period = ("~" if approx else "") + str(y) + (f"-{m:02d}" if m else "")
    return {"form": info["form"], "agency": info["agency"], "kind": kind, "period": period,
            "match": in_range(y, m, rng)}


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


def _wait_change(page, frame, before, timeout=12):
    end = time.time() + timeout
    while time.time() < end:
        try:
            page.wait_for_timeout(300)
            if frame.evaluate(TABLE_SIG_JS) != before:
                wait_records(page, timeout=8000)
                return True
        except Exception:
            pass
    return False


def max_page_size(page, frame, log=None):
    """ตั้งจำนวนรายการต่อหน้าให้มากที่สุด (ถ้ามีตัวเลือก)"""
    try:
        before = frame.evaluate(TABLE_SIG_JS)
        r = frame.evaluate(PAGESIZE_JS)
        if r == "matselect":
            page.wait_for_timeout(400)
            v = frame.evaluate(PICK_MAX_OPTION_JS)
            if not v:
                _close_menu(page)
                return
            r = f"select:{v}"
        if r:
            _wait_change(page, frame, before, timeout=8)
            if log:
                log(f"ตั้งให้แสดง {r.split(':')[-1]} รายการต่อหน้า")
    except Exception:
        pass


def next_page(page, frame, diag=None):
    """กดไปหน้าถัดไปของตาราง คืน True ถ้าเปลี่ยนหน้าได้"""
    try:
        r = frame.evaluate(NEXT_JS) or {}
        if diag is not None:
            diag.append(f"ปุ่มหน้าถัดไป: {r.get('how')} {r.get('html', '') or r.get('pag', '')}")
        if r.get("how") not in ("next", "number"):
            return False
        before = frame.evaluate(TABLE_SIG_JS)
        frame.locator('[data-taxdocs-next="1"]').first.click(timeout=6000)
        ok = _wait_change(page, frame, before)
        if diag is not None and not ok:
            diag.append("กดหน้าถัดไปแล้วแต่ตารางไม่เปลี่ยน")
        return ok
    except Exception as e:
        if diag is not None:
            diag.append(f"กดหน้าถัดไปไม่ได้: {str(e).splitlines()[0][:80]}")
        return False


def load_all_rows(page, frame, diag=None):
    """กด 'แสดงเพิ่มเติม' / เลื่อนลงจนรายการไม่เพิ่ม (ตารางที่โหลดเพิ่มทีละช่วง)"""
    for i in range(30):
        try:
            before = frame.evaluate(TABLE_SIG_JS)
            how = frame.evaluate(MORE_JS)
            if not how:
                return
            page.wait_for_timeout(700)
            if not _wait_change(page, frame, before, timeout=3):
                return
            if diag is not None:
                diag.append(f"โหลดรายการเพิ่ม ({how}) ครั้งที่ {i + 1}")
        except Exception:
            return


def _scan_one(ctx, page, rng, refs, info, stamp, pg, progress):
    """อ่านหน้าปัจจุบันของตาราง (ทุก frame): ปุ่ม/ลิงก์ + เมนู ⋮"""
    out = []
    for fi, f in enumerate(page.frames):
        try:
            data = f.evaluate(SCAN_JS, f"td{stamp}_{pg}_{fi}_")
        except Exception:
            continue
        info["junk"] += data.get("junk", 0)
        info["login"] = info["login"] or data.get("loginForm", False)
        if fi == 0 and pg == 1:
            info["pages"].append(data.get("title") or data.get("url"))
        for it in data.get("items", []):
            it.update(page_url=data.get("url", ""), page_title=data.get("title", ""), pg=pg, frame_i=fi,
                      sig=it["row"][:100])
            it.update(describe(it, rng))
            refs[it["id"]] = f
            out.append(it)
    for fi, f in enumerate(page.frames):
        try:
            toggles = f.evaluate(TOGGLES_JS, f"tm{stamp}_{pg}_{fi}_")
        except Exception:
            continue
        for ti, tg in enumerate(toggles[:100]):
            progress(f"หน้า {pg}: กำลังอ่านแถวที่ {ti + 1}/{len(toggles)}")
            row = tg["row"]
            pre = describe({"row": row, "label": "", "head": tg.get("head", "")}, rng)
            if not td.KW_MONTH.search(td.normalize(row)):
                try:
                    row = f.evaluate(ROW_DETAIL_JS, tg["id"]) or row     # กางแถวเพื่ออ่านเดือนภาษี
                    pre = describe({"row": row, "label": "", "head": tg.get("head", "")}, rng)
                except Exception:
                    pass
            if rng and not pre["match"]:
                info["out_of_range"] += 1
                info["rows_out"].append(pre["period"])
                continue
            try:
                labels = _menu_items(f, tg["id"], page)
            except Exception:
                _close_menu(page)
                continue
            for li, lab in enumerate(labels):
                subs = split_choices(lab) or [None]
                for si, sub in enumerate(subs):
                    it = {"id": f"{tg['id']}m{li}s{si}", "label": f"{lab} → {sub}" if sub else lab,
                          "href": "", "onclick": "", "row": row,
                          "head": tg.get("head", ""), "isPdf": False, "tag": "menu",
                          "menu": {"toggle": tg["id"], "text": lab}, "sub": sub, "sig": tg["row"][:100],
                          "page_url": page.url, "page_title": "", "pg": pg, "frame_i": fi}
                    it.update(describe(dict(it, label=sub or lab), rng))
                    refs[it["id"]] = f
                    out.append(it)
                    info["menus"] += 1
        info["rows"] += len(toggles)
    return out


def scan_pages(ctx, rng, set_year, refs, log, pages=None, progress=None, max_pages=60, debug=None, debug_dir=None):
    """อ่านตารางในหน้าเว็บให้ครบทุกหน้า คืนรายการเอกสาร
    rng = ((ปี,เดือน),(ปี,เดือน)) ช่วงเดือนภาษีที่ต้องการ; refs[id] = frame สำหรับดาวน์โหลดภายหลัง"""
    progress = progress or (lambda m: None)
    results = []
    pages = [p for p in (pages or ctx.pages) if not p.is_closed() and p.url.startswith("http")]
    years = sorted({rng[0][0], rng[1][0]}) if rng else []
    if set_year and years:
        for p in pages:
            for f in p.frames:
                try:
                    r = f.evaluate(SET_YEAR_JS, years_for(years[-1]))
                    if r.get("changed"):
                        log(f"ตั้งปี {years[-1]} ในหน้าเว็บแล้ว" + (f" และกด '{r['clicked']}'" if r.get("clicked") else ""))
                except Exception:
                    pass
        for p in pages:
            wait_records(p, timeout=8000)
    stamp = str(int(time.time() * 1000) % 10_000_000)
    info = {"pages": [], "junk": 0, "login": False, "menus": 0, "rows": 0, "out_of_range": 0,
            "rows_out": [], "table_pages": 0}
    diag = []
    for p in pages:
        max_page_size(p, p.main_frame, lambda m: (log(m), diag.append(m)))
        pg = 1
        while True:
            progress(f"กำลังอ่านหน้า {pg}…")
            load_all_rows(p, p.main_frame, diag)
            rows_before = info["rows"]
            found = _scan_one(ctx, p, rng, refs, info, stamp, pg, progress)
            results += found
            info["table_pages"] = max(info["table_pages"], pg)
            diag.append(f"หน้า {pg}: {info['rows'] - rows_before} แถว, ได้ {len(found)} รายการในช่วง")
            if pg >= max_pages:
                break
            if not next_page(p, p.main_frame, diag):
                break
            pg += 1
        p._taxdocs_pg = pg            # จำหน้าปัจจุบันไว้ (ใช้ตอนดาวน์โหลด)
        if debug:
            shot = ""
            try:
                if debug_dir:
                    debug_dir.mkdir(exist_ok=True)
                    p.evaluate("(document.scrollingElement||document.body).scrollTop=1e9")
                    p.wait_for_timeout(300)
                    shot = str(debug_dir / "scan_bottom.jpg")
                    p.screenshot(path=shot, type="jpeg", quality=70)
            except Exception:
                shot = ""
            debug([f"[ค้นหา] {p.url[:100]} | ช่วง {rng}"] + [f"   {d}" for d in diag] +
                  [f"   รวม {pg} หน้า, {info['rows']} แถว, ข้ามนอกช่วง {info['out_of_range']}" + (f" (ภาพ: {shot})" if shot else ""), ""])
    uniq, seen = [], set()
    for it in results:
        key = (it["href"] or "", it["row"][:120], it["label"], it.get("pg"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    scan_pages.last_info = info
    return uniq


def ensure_page(page, frame, item, reset):
    """ไปหน้าที่รายการนี้อยู่ (ถ้าตารางมีหลายหน้า) แล้วหาปุ่มใหม่"""
    want = int(item.get("pg") or 1)
    cur = getattr(page, "_taxdocs_pg", 1)
    moved = False
    if want < cur:
        reset()                      # กลับไปหน้าแรก
        max_page_size(page, page.main_frame)
        cur = 1
        moved = True
    while cur < want:
        if not next_page(page, page.main_frame):
            break
        cur += 1
        moved = True
    page._taxdocs_pg = cur
    if cur != want:
        return False
    if item.get("menu"):
        has = frame.locator(f'[data-taxdocs-menu="{item["menu"]["toggle"]}"]').count() > 0
        if moved or not has:
            new_id = item["menu"]["toggle"] + "r"
            if frame.evaluate(RELOCATE_JS, [item["sig"], new_id]):
                item["menu"]["toggle"] = new_id
            else:
                return False
    return True


def _decode(b64):
    try:
        return base64.b64decode(b64 + "=" * (-len(b64) % 4))
    except Exception:
        return b""


def _ext(data):
    if data[:5] == b"%PDF-":
        return ".pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    return ""


def fetch(ctx, frame, item, keep, counter, log, debug=None, debug_dir=None):
    """ดาวน์โหลดรายการเดียว คืน (สำเร็จ?, ข้อความ)"""
    hint = item["row"]
    meta = {"kind": item["kind"], "form": item["form"], "period": item["period"]}
    base_name = td.safe((item["label"] or "document")[:40])
    name = base_name + ".pdf"
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
    page = frame.page
    # ปิดหน้าต่าง/เมนูที่เว็บเปิดค้างจากรายการก่อน (เช่น กล่องแสดงภาพใบเสร็จ) ไม่ให้บังปุ่ม
    for _ in range(2):
        _close_menu(page)
    # บันทึกสิ่งที่เว็บทำหลังกด (ใช้วิเคราะห์ถ้าไม่ได้ไฟล์)
    seen_resp = []

    def on_resp(r):
        try:
            seen_resp.append(f"{r.status} {(r.headers.get('content-type') or '-').split(';')[0]} "
                             f"{r.headers.get('content-length') or '?'}B {r.request.method} {td.urlparse(r.url).path[-90:]}")
        except Exception:
            pass
    ctx.on("response", on_resp)
    try:
        since = page.evaluate("Date.now()")
    except Exception:
        since = 0
    before_pages = set(ctx.pages)
    before = counter["n"]
    counter["hint"], counter["meta"] = hint, meta

    def finish(ok, note):
        try:
            ctx.remove_listener("response", on_resp)
        except Exception:
            pass
        for np_ in [p for p in ctx.pages if p not in before_pages]:
            try:
                np_.close()
            except Exception:
                pass
        counter["hint"], counter["meta"] = "", None
        overlay_txt, shot = "", ""
        if debug and not ok:
            try:
                ov = page.evaluate(OVERLAYS_JS)
                overlay_txt = " || ".join(f"<{o['tag']} {o['c']}> {o['t']}" for o in ov) or "-"
            except Exception:
                overlay_txt = "?"
            try:
                if debug_dir:
                    debug_dir.mkdir(exist_ok=True)
                    shot = str(debug_dir / "last_fail.jpg")
                    page.screenshot(path=shot, type="jpeg", quality=70)
            except Exception:
                shot = ""
        for f_ in page.frames:                    # ปิดกล่องที่ยังค้างอยู่ (หลังถ่ายภาพไว้แล้ว)
            try:
                f_.evaluate(CLOSE_OVERLAY_JS)
            except Exception:
                pass
        if debug and not ok:
            debug([f"รายการ: {item['label']} | แถว: {item['row'][:120]}",
                   f"บนหน้าจอหลังกด: {overlay_txt}" + (f" (ภาพ: {shot})" if shot else ""),
                   f"หน้าใหม่ที่เปิด: {[p.url[:100] for p in new_pages] or '-'}",
                   f"สั่งพิมพ์ (window.print): {'ใช่' if printed else 'ไม่'}",
                   f"กล่องเลือกรายการ: {' / '.join(modal_log) if modal_log else 'ไม่พบ'}",
                   "การตอบกลับจากเว็บหลังกด:"] + [f"   {x}" for x in seen_resp[-40:]] + [f"ผล: {note}", ""])
        return ok, note

    new_pages, printed = [], False
    try:
        if item.get("menu"):
            tog = frame.locator(f'[data-taxdocs-menu="{item["menu"]["toggle"]}"]')
            if tog.count() == 0:
                return finish(False, "หาไม่พบในหน้าเว็บ (หน้าเปลี่ยนไปแล้ว) — กดค้นหาใหม่")
            tog.first.scroll_into_view_if_needed(timeout=5000)
            try:
                tog.first.click(timeout=6000)
            except Exception:
                _close_menu(page)                     # มีอะไรบังอยู่ → ปิดแล้วลองใหม่
                page.mouse.click(5, 5)
                tog.first.click(timeout=6000)
            page.wait_for_timeout(500)
            clicked = False
            for f in page.frames:
                try:
                    f.evaluate(PREMARK_JS)
                except Exception:
                    pass
            for f in [frame] + [x for x in page.frames if x is not frame]:
                try:
                    if f.evaluate(CLICK_MENU_ITEM_JS, item["menu"]["text"]):
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                _close_menu(page)
                return finish(False, "ไม่พบรายการในเมนู — กดค้นหาใหม่")
        else:
            loc = frame.locator(f'[data-taxdocs-id="{item["id"]}"]')
            if loc.count() == 0:
                return finish(False, "หาไม่พบในหน้าเว็บ (หน้าเปลี่ยนไปแล้ว) — กดค้นหาใหม่")
            for f in page.frames:
                try:
                    f.evaluate(PREMARK_JS)
                except Exception:
                    pass
            loc.first.scroll_into_view_if_needed(timeout=5000)
            loc.first.click(timeout=10000)
    except Exception as e:
        return finish(False, f"กดปุ่มไม่ได้: {str(e).splitlines()[0][:80]}")

    def grab_blobs():
        """ไฟล์ที่เว็บสร้างในหน้า (blob:/data:) — ทั้งหน้าเดิมและหน้าที่เปิดใหม่"""
        nonlocal printed
        got = False
        for pg_ in [page] + [p for p in ctx.pages if p not in before_pages]:
            for f in pg_.frames:
                try:
                    r = f.evaluate(READ_BLOBS_JS, since)
                except Exception:
                    continue
                if r.get("printed", 0) >= since > 0:
                    printed = True
                for b in r.get("items", []):
                    data = _decode(b.get("b64", ""))
                    ext = _ext(data)
                    if ext:
                        keep(data, base_name + ext, pg_.url, hint, meta)
                        got = True
        return got

    taken_pages = set()

    def grab_newpages():
        """แท็บใหม่ที่เปิดไฟล์ตรง ๆ (รูป/PDF) → ดึงไฟล์ด้วย session เดียวกัน"""
        for np_ in [p for p in ctx.pages if p not in before_pages and p not in taken_pages]:
            u = np_.url
            if not u.startswith(("http", "blob:", "data:")):
                continue
            try:
                if u.startswith("http"):
                    data = ctx.request.get(u, timeout=30000).body()
                else:
                    data = _decode(np_.evaluate(FETCH_B64_JS, u) or "")
            except Exception:
                continue
            ext = _ext(data)
            if ext:
                taken_pages.add(np_)
                keep(data, base_name + ext, u if u.startswith("http") else page.url, hint, meta)
                return True
        return False

    def wait_file(seconds):
        end = time.time() + seconds
        n0 = counter["n"]
        while time.time() < end:
            if counter["n"] > n0 or grab_blobs() or grab_newpages():
                return True
            try:
                page.wait_for_timeout(400)
            except Exception:
                time.sleep(0.4)
        return False

    modal_note = ""
    modal_log = []

    def handle_modal():
        """กล่อง 'เลือกรายการ' ที่มีปุ่มดาวน์โหลดหลายปุ่ม → กดปุ่มของรายการที่ต้องการ แล้วปิดกล่อง"""
        nonlocal modal_note
        mark = f"md{int(time.time() * 1000) % 100000}_"
        for f in page.frames:
            try:
                m = f.evaluate(MODAL_JS, mark)
            except Exception:
                continue
            if not m or not m.get("items"):
                continue
            choices = [x for x in m["items"] if not is_bad(x["text"] + " " + x["btn"])]
            modal_log.append(f"พบกล่อง '{m.get('title') or '-'}': " + " | ".join(f"{x['text']} [{x['btn']}]" for x in m["items"]))
            sub = item.get("sub")
            targets = [x for x in choices if _sub_match(sub, x["text"])] if sub else choices
            if sub and not targets:
                modal_note = f"ในกล่อง '{m.get('title') or 'เลือกรายการ'}' ไม่มี '{sub}' (มี: {', '.join(x['text'] for x in choices)})"
            got = False
            for x in targets:
                try:
                    f.locator(f'[data-taxdocs-mdl="{x["id"]}"]').first.click(timeout=6000)
                    res = wait_file(20)
                    modal_log.append(f"กด '{x['btn']}' ของ '{x['text']}' → {'ได้ไฟล์' if res else 'ไม่ได้ไฟล์'}")
                    got = got or res
                except Exception as e:
                    modal_log.append(f"กด '{x['text']}' ไม่ได้: {str(e).splitlines()[0][:80]}")
            try:
                if m.get("hasClose"):
                    f.locator(f'[data-taxdocs-close="{mark}"]').first.click(timeout=4000)
                else:
                    _close_menu(page)
            except Exception:
                _close_menu(page)
            return True, got
        return False, False

    deadline = time.time() + 25
    ok = False
    while time.time() < deadline:
        if counter["n"] > before:
            ok = True
            break
        new_pages = [p for p in ctx.pages if p not in before_pages]
        if grab_blobs() or grab_newpages():
            ok = True
            break
        if printed:
            break
        found, got = handle_modal()
        if found:
            ok = got or counter["n"] > before
            break
        try:
            page.wait_for_timeout(500)
        except Exception:
            time.sleep(0.5)
    new_pages = [p for p in ctx.pages if p not in before_pages]
    # 3) PDF ที่แสดงในกรอบ (iframe/embed) หรือแท็บใหม่แบบ blob:
    if not ok:
        targets = [(np_, np_.url) for np_ in new_pages if np_.url.startswith(("blob:", "data:"))]
        for pg_ in [page] + new_pages:
            try:
                targets += [(pg_, u) for u in pg_.evaluate(BLOB_SRC_JS)]
            except Exception:
                pass
        for pg_, u in targets:
            try:
                data = _decode(pg_.evaluate(FETCH_B64_JS, u) or "")
                ext = _ext(data)
                if ext:
                    keep(data, base_name + ext, pg_.url, hint, meta)
                    ok = True
                    break
            except Exception:
                pass
    # 4) เว็บสั่งพิมพ์ หรือเปิดหน้าพิมพ์ (HTML) → แปลงหน้านั้นเป็น PDF
    if not ok:
        cands = [p for p in new_pages if p.url.startswith("http")]
        if printed:
            cands.append(page)
        for np_ in cands:
            try:
                np_.wait_for_load_state("load", timeout=10000)
                cdp = ctx.new_cdp_session(np_)
                res = cdp.send("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True})
                data = base64.b64decode(res["data"])
                if data[:5] == b"%PDF-":
                    keep(data, base_name + ".pdf", np_.url, hint, meta)
                    ok = True
                    break
            except Exception as e:
                log(f"แปลงหน้าพิมพ์เป็น PDF ไม่ได้: {str(e).splitlines()[0][:80]}")
    if ok:
        try:
            page.keyboard.press("Escape")       # ปิดหน้าต่างแสดงเอกสารบนเว็บ (ถ้ามี)
        except Exception:
            pass
        return finish(True, "ดาวน์โหลดแล้ว")
    return finish(False, modal_note or "ไม่ได้รับไฟล์ — กด 'ดูบันทึก' เพื่อดูว่าเว็บทำอะไร แล้วส่งให้ช่วยปรับ")
