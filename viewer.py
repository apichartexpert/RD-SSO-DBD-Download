# -*- coding: utf-8 -*-
"""
แสดงเว็บไซต์ (RD / SSO / DBD) ภายในหน้าโปรแกรม

เบราว์เซอร์ทำงานเบื้องหลัง (ไม่มีหน้าต่างแยก) โปรแกรมส่งภาพหน้าจอของแท็บที่เลือก
มาแสดงในหน้าเว็บของโปรแกรม และส่งการคลิก/พิมพ์ของผู้ใช้กลับไปที่เบราว์เซอร์

ทุกฟังก์ชันที่แตะ Playwright ต้องเรียกจาก thread ของเบราว์เซอร์เท่านั้น
(ยกเว้น latest_frame / answer_dialog / snapshot ซึ่งปลอดภัยข้าม thread)
"""
import base64
import queue
import threading
import time

SPECIAL_KEYS = {
    "Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp",
    "ArrowDown", "Home", "End", "PageUp", "PageDown", "Insert", "F5",
}

# หา input ชื่อผู้ใช้/รหัสผ่านในหน้า (ทุก frame)
FIND_LOGIN_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !e.disabled; };
  const pw = [...document.querySelectorAll('input[type=password]')].filter(vis);
  if (!pw.length) return null;
  const p = pw[0];
  const scope = p.form || document;
  const texts = [...scope.querySelectorAll('input:not([type]), input[type=text], input[type=email], input[type=tel], input[type=number]')]
                .filter(vis).filter(e => !/(captcha|otp|code|รหัสภาพ|search|ค้นหา)/i.test((e.name||'')+(e.id||'')+(e.placeholder||'')));
  // เลือกช่องที่อยู่ก่อนช่องรหัสผ่าน (ตามลำดับในหน้า)
  let u = null;
  for (const t of texts) { if (t.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_FOLLOWING) u = t; }
  if (!u && texts.length) u = texts[0];
  p.setAttribute('data-taxdocs-pw', '1');
  if (u) u.setAttribute('data-taxdocs-user', '1');
  return {hasUser: !!u, userVal: u ? u.value : '', pwVal: p.value, pwEmpty: !p.value};
}
"""


class View:
    def __init__(self, ctx, on_status):
        self.ctx = ctx
        self.on_status = on_status
        self.lock = threading.Condition()
        self.frame = None          # (seq, jpeg bytes, width, height)
        self.seq = 0
        self.active = None         # Page ที่แสดงอยู่
        self.cdp = None
        self.size = {"width": 1280, "height": 800}
        self.inputs = queue.Queue()
        self.dialog = None         # {"type","message","event","accept","text"}
        self.tabs_info = []        # snapshot สำหรับ thread อื่น
        self.url = ""
        self.loading = False
        self.on_page_loaded = None

    # ---------------------------------------------------------- ภาพหน้าจอ
    def _on_frame(self, params):
        try:
            data = base64.b64decode(params["data"])
            meta = params.get("metadata", {})
            with self.lock:
                self.seq += 1
                self.frame = (self.seq, data, meta.get("deviceWidth"), meta.get("deviceHeight"))
                self.lock.notify_all()
        except Exception:
            pass
        try:
            self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:
            pass

    def latest_frame(self, after, timeout=8.0):
        """รอภาพใหม่ (เรียกจาก thread ของ HTTP ได้)"""
        end = time.time() + timeout
        with self.lock:
            while (self.frame is None or self.frame[0] <= after) and time.time() < end:
                self.lock.wait(max(0.05, end - time.time()))
            return self.frame

    def _start_cast(self):
        if self.cdp is not None:
            try:
                self.cdp.send("Page.stopScreencast")
                self.cdp.detach()
            except Exception:
                pass
            self.cdp = None
        if not self.active or self.active.is_closed():
            return
        try:
            self.active.set_viewport_size(self.size)
        except Exception:
            pass
        try:
            self.cdp = self.ctx.new_cdp_session(self.active)
            self.cdp.on("Page.screencastFrame", self._on_frame)
            self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 75,
                                                  "maxWidth": self.size["width"], "maxHeight": self.size["height"],
                                                  "everyNthFrame": 1})
        except Exception as e:
            self.on_status(f"แสดงหน้าเว็บไม่ได้: {str(e).splitlines()[0][:80]}")

    def _refresh_frame(self):
        """ขอภาพใหม่ทันที (บางครั้งหน้าไม่เปลี่ยน screencast จะไม่ส่งภาพ)"""
        try:
            img = self.active.screenshot(type="jpeg", quality=75, timeout=5000)
            with self.lock:
                self.seq += 1
                self.frame = (self.seq, img, self.size["width"], self.size["height"])
                self.lock.notify_all()
        except Exception:
            pass

    # ---------------------------------------------------------- แท็บ
    def attach_page(self, page, activate=True):
        page.on("dialog", lambda d: self._on_dialog(d))
        page.on("close", lambda *_: self._on_close(page))
        page.on("load", lambda *_: self._on_load(page))
        page.on("framenavigated", lambda fr: self._sync_tabs() if fr == page.main_frame else None)
        if activate:
            self.select(page)
        self._sync_tabs()

    def _on_load(self, page):
        self._sync_tabs()
        if self.on_page_loaded:
            try:
                self.on_page_loaded(page)
            except Exception:
                pass

    def _on_close(self, page):
        if page is self.active:
            others = [p for p in self.ctx.pages if not p.is_closed() and p is not page]
            self.active = None
            if others:
                self.select(others[-1])
        self._sync_tabs()

    def select(self, page):
        self.active = page
        try:
            page.bring_to_front()
        except Exception:
            pass
        self._start_cast()
        self._refresh_frame()
        self._sync_tabs()

    def pages(self):
        return [p for p in self.ctx.pages if not p.is_closed()]

    def _sync_tabs(self):
        info = []
        for i, p in enumerate(self.pages()):
            try:
                title = p.title()
            except Exception:
                title = ""
            info.append({"i": i, "title": title or p.url, "url": p.url, "active": p is self.active})
        self.tabs_info = info
        self.url = self.active.url if self.active and not self.active.is_closed() else ""

    # ---------------------------------------------------------- กล่องข้อความของเว็บ (alert/confirm)
    def _on_dialog(self, d):
        ev = threading.Event()
        self.dialog = {"type": d.type, "message": d.message, "default": d.default_value,
                       "event": ev, "accept": False, "text": None}
        self.on_status(f"เว็บแสดงข้อความ: {d.message[:80]}")
        ev.wait(180)
        info, self.dialog = self.dialog, None
        try:
            if info and info["accept"]:
                d.accept(info["text"] if info["text"] is not None else d.default_value)
            else:
                d.dismiss()
        except Exception:
            pass

    def answer_dialog(self, accept, text=None):
        dg = self.dialog
        if dg:
            dg["accept"], dg["text"] = bool(accept), text
            dg["event"].set()
            return True
        return False

    # ---------------------------------------------------------- การคลิก/พิมพ์ (ใส่คิวจาก HTTP, ทำงานใน thread เบราว์เซอร์)
    def queue_input(self, ev):
        self.inputs.put(ev)

    def process_inputs(self):
        n = 0
        while not self.inputs.empty() and n < 200:
            ev = self.inputs.get_nowait()
            n += 1
            try:
                self._handle(ev)
            except Exception as e:
                self.on_status(f"ส่งคำสั่งไปเว็บไม่ได้: {str(e).splitlines()[0][:80]}")
        return n

    def _handle(self, ev):
        t = ev.get("t")
        pages = self.pages()
        if t == "tab":
            i = int(ev.get("i", 0))
            if 0 <= i < len(pages):
                self.select(pages[i])
            return
        if t == "closetab":
            i = int(ev.get("i", -1))
            if 0 <= i < len(pages) and len(pages) > 1:
                pages[i].close()
            return
        if t == "resize":
            w = max(800, min(1920, int(ev.get("w", 1280))))
            h = max(500, min(1400, int(ev.get("h", 800))))
            if (w, h) != (self.size["width"], self.size["height"]):
                self.size = {"width": w, "height": h}
                self._start_cast()
            return
        p = self.active
        if not p or p.is_closed():
            return
        if t == "move":
            p.mouse.move(ev["x"], ev["y"])
        elif t == "down":
            p.mouse.move(ev["x"], ev["y"])
            p.mouse.down(button=ev.get("button", "left"), click_count=int(ev.get("n", 1)))
        elif t == "up":
            p.mouse.move(ev["x"], ev["y"])
            p.mouse.up(button=ev.get("button", "left"), click_count=int(ev.get("n", 1)))
        elif t == "dbl":
            p.mouse.dblclick(ev["x"], ev["y"])
        elif t == "wheel":
            p.mouse.move(ev["x"], ev["y"])
            p.mouse.wheel(ev.get("dx", 0), ev.get("dy", 0))
        elif t == "text":
            if ev.get("text"):
                p.keyboard.insert_text(ev["text"])
        elif t == "key":
            key = ev.get("key", "")
            mods = [m for m in ("Control", "Alt", "Shift", "Meta") if ev.get(m.lower())]
            if key in SPECIAL_KEYS or mods or (len(key) > 1 and key.startswith("F")):
                p.keyboard.press("+".join(mods + [key]))
        elif t == "nav":
            what = ev.get("what")
            if what == "back":
                p.go_back(timeout=30000)
            elif what == "forward":
                p.go_forward(timeout=30000)
            elif what == "reload":
                p.reload(timeout=60000)
            elif what == "go" and ev.get("url"):
                url = ev["url"].strip()
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                p.goto(url, timeout=60000)
        self._sync_tabs()

    # ---------------------------------------------------------- ล็อกอิน
    def find_login(self, page=None):
        """คืน (frame, info) ของฟอร์มล็อกอินในหน้า หรือ (None, None)"""
        page = page or self.active
        if not page or page.is_closed():
            return None, None
        for f in page.frames:
            try:
                info = f.evaluate(FIND_LOGIN_JS)
            except Exception:
                continue
            if info:
                return f, info
        return None, None

    def fill_login(self, username, password, page=None):
        f, info = self.find_login(page)
        if not f:
            return False, "ไม่พบช่องชื่อผู้ใช้/รหัสผ่านในหน้านี้"
        if username and info.get("hasUser"):
            f.locator('[data-taxdocs-user="1"]').first.fill(username)
        if password:
            f.locator('[data-taxdocs-pw="1"]').first.fill(password)
        self._refresh_frame()
        return True, "กรอกให้แล้ว — ตรวจรหัสภาพ/OTP (ถ้ามี) แล้วกดเข้าสู่ระบบเอง"

    def read_login(self, page=None):
        """อ่านชื่อผู้ใช้/รหัสผ่านที่ผู้ใช้พิมพ์ไว้ในหน้า (ใช้ตอนกด 'จำรหัส')"""
        f, info = self.find_login(page)
        if not f:
            return None, None
        user = ""
        if info.get("hasUser"):
            user = f.locator('[data-taxdocs-user="1"]').first.input_value()
        pw = f.locator('[data-taxdocs-pw="1"]').first.input_value()
        return user, pw

    def snapshot(self):
        dg = self.dialog
        return {"tabs": self.tabs_info, "url": self.url, "size": self.size, "seq": self.seq,
                "dialog": {"type": dg["type"], "message": dg["message"]} if dg else None}
