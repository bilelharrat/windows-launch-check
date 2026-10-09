"""Records J.A.R.V.I.S. Daredevil on this Windows machine for the askeden.com video: the real app and real
answers from Claude; the camera fed a photo of a parking notice; a demo mailbox on this machine (a real IMAP
and SMTP server, video/mailserver.py) with an email from a colleague. Requests are typed (the voice that asks
is added in the edit); Jarvis's replies are read from the window with their times. Writes out/raw.mp4 and
out/timeline.json."""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
import websocket

sys.path.insert(0, str(Path(__file__).parent))
from mailserver import TestMail, make_raw, when

EXE = Path(sys.argv[1]); OUT = Path("out"); OUT.mkdir(exist_ok=True)
VIDEO = Path("video"); PORT = 9333
ME, PASSWORD = "sam.rivera@example.edu", "demo-app-password"
timeline = []
t0 = None
def now(): return time.time() - t0
def note(kind, text): timeline.append({"t": round(now(), 2), "kind": kind, "text": text}); print(f"{now():6.1f}s {kind}: {text[:300]}", flush=True)

# the demo mailbox: one new email from a colleague
mail = TestMail()
mail.store.users = {ME: PASSWORD}
mail.store.add("INBOX", make_raw(
    "Maya Chen <maya.chen@example.edu>", f"Sam Rivera <{ME}>", "Reading group on Thursday",
    "Hi Sam,\n\nCould we move Thursday's reading group to 2 pm? The seminar room is booked in the morning.\n\n"
    "Also, could you bring the sleep and memory papers you mentioned?\n\nThanks,\nMaya", date=when(0.02)))

class Page:
    def __init__(self, ws_url): self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True); self.n = 0
    def call(self, method, **params):
        self.n += 1; self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n: return m.get("result", {})
    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return (r.get("result") or {}).get("value")
    def key(self, key, code, vk, text="", modifiers=0):
        for t in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, key=key, code=code, windowsVirtualKeyCode=vk, modifiers=modifiers, **({"text": text} if text and t == "keyDown" else {}))

def targets():
    try: return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3))
    except Exception: return []
def browser_call(ws, n, method, **params):
    ws.send(json.dumps({"id": n, "method": method, "params": params}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == n: return m

y4m = OUT / "letter.y4m"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(VIDEO / "letter.png"), "-t", "20", "-r", "15", "-vf", "scale=1280:720,format=yuv420p", str(y4m)], check=True)

rec = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "gdigrab", "-framerate", "30", "-i", "desktop", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(OUT / "raw.mp4")], stdin=subprocess.PIPE)
time.sleep(2); t0 = time.time()
app = subprocess.Popen([str(EXE), f"--remote-debugging-port={PORT}", "--use-fake-device-for-media-stream", f"--use-file-for-fake-video-capture={y4m.resolve()}", "--use-fake-ui-for-media-stream"])
note("open", "the app starts")

page = None
for _ in range(240):
    pages = [t for t in targets() if t.get("type") == "page" and t.get("url", "").startswith("http://127.0.0.1")]
    if pages: page = Page(pages[0]["webSocketDebuggerUrl"]); break
    time.sleep(1)
if not page: print("the page never came"); rec.communicate(b"q"); sys.exit(1)
note("ready", "the page is up")

# the window over the whole display: the app's biggest visible window (found by its process), no frame, screen size
try:
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    class R(ctypes.Structure): _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    def rect(h): r = R(); u.GetWindowRect(h, ctypes.byref(r)); return (r.l, r.t, r.r, r.b)
    def windows():
        found = []
        def each(h, _):
            pid = wintypes.DWORD(); u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == app.pid and u.IsWindowVisible(h):
                l, t, r, b = rect(h); found.append(((r - l) * (b - t), h))
            return True
        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(each), 0)
        return sorted(found, reverse=True)
    h = 0
    for _ in range(30):
        ws_ = windows()
        if ws_: h = ws_[0][1]; break
        time.sleep(0.5)
    print("window", h, "before", rect(h) if h else None, "screen", sw, sh)
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.ShowWindow(h, 1)
    print("style", u.SetWindowLongPtrW(h, -16, 0x10000000), ctypes.GetLastError())   # WS_VISIBLE only: no frame
    for _ in range(2):
        print("pos", u.SetWindowPos(h, None, 0, 0, sw, sh, 0x0020 | 0x0040 | 0x0004), ctypes.GetLastError())
        time.sleep(1.0)
    time.sleep(0.5)
    print("window rect:", rect(h), "page", page.js("[innerWidth, innerHeight]"))
    timeline.append({"t": 0, "kind": "window", "text": ",".join(map(str, rect(h)))})
except Exception as e: print("full screen:", repr(e))

page.js("window.__mail = []; window.jarvisFeatures.on('mail_check', (ev) => window.__mail.push(ev)); window.jarvisFeatures.on('mail_accounts', (ev) => window.__mail.push({accounts: (ev.accounts || []).length})); true")
for i in range(240):   # the app's own connection up and answering (a command sent before is lost)
    if page.js("send({type: 'mail_status'}) && true") and '"accounts"' in (page.js("JSON.stringify(window.__mail)") or ""): break
    time.sleep(1)
print("connected after", i, "s", flush=True)
# Claude, the demo mailbox, and spoken punctuation, the way the app's own settings give them (nothing on screen)
page.js(f"send({{type: 'signin_key', key: {json.dumps(os.environ.get('ANTHROPIC_API_KEY', ''))}}}); true")
page.js("send({type: 'mail_save', address: %s, password: %s, name: 'Sam Rivera', imap_host: '127.0.0.1', imap_port: %d, "
        "imap_security: 'none', smtp_host: '127.0.0.1', smtp_port: %d, smtp_security: 'none'}); true" % (json.dumps(ME), json.dumps(PASSWORD), mail.imap_port, mail.smtp_port))
page.js("send({type: 'feature_prefs', changes: {a11y_dictate_punct: 'spoken'}}); true")
for _ in range(30):
    got = page.js("JSON.stringify(window.__mail)") or "[]"
    if '"saved":true' in got or '"ok":false' in got: break
    time.sleep(1)
print("mail:", got[:600], "logins:", getattr(mail.store, "logins", "?"), flush=True)
for _ in range(3): page.key("Escape", "Escape", 27); time.sleep(0.4)   # (the first-run setup)
time.sleep(1.5)

def reply_text(): return page.js("(document.getElementById('reply') || {}).innerText || ''") or ""
def ask(request, limit=150, approve=False):
    page.js("(() => { const b = document.getElementById('ask-input'); if (b) { b.focus(); b.value = ''; } return !!b; })()")
    time.sleep(0.6)
    before = reply_text()
    note("you", request)
    for ch in request:
        page.call("Input.insertText", text=ch); time.sleep(0.04)
    time.sleep(0.5); page.key("Enter", "Enter", 13, "\r")
    started = time.time(); prev = ""; changed = time.time(); first = None; approved = False
    while time.time() - started < limit:
        if approve and not approved and page.js("!!document.querySelector('[data-approval]')"):
            time.sleep(4)   # (time to read the card)
            note("approval", page.js("(document.querySelector('[data-approval]') || {}).innerText || ''") or "")
            note("you", "Yes, send it.")
            page.key("Y", "KeyY", 89, modifiers=1 | 8)  # Alt+Shift+Y, Daredevil's yes
            approved = True; started = time.time(); before = reply_text(); prev = ""; first = None
        text = reply_text()
        state = page.js("document.body.dataset.state || ''")
        if text and text != before and text != prev:
            prev, changed = text, time.time()
            if first is None: first = now()
        if prev and state == "idle" and time.time() - changed > 6 and time.time() - started > 12 and (approved or not approve): break
        time.sleep(0.5)
    if prev:
        timeline.append({"t": round(first, 2), "kind": "jarvis", "text": " ".join(prev.split())})
        print(f"{first:6.1f}s jarvis: {prev[:400]}", flush=True)
    time.sleep(2)

ask("Find the three most cited papers on sleep and memory since 2015.")
ask("Look through my camera and read me this parking notice.")
ask("Read me my new email.")
ask("Reply: Thursday at two works comma and I'll bring the papers period See you then period", approve=True)

time.sleep(2); total = now()
rec.communicate(b"q", timeout=60)
(OUT / "timeline.json").write_text(json.dumps(timeline, indent=1))
print("recorded", round(total, 1), "s; sent:", [r[1] for r in mail.store.sent])
for frm, to, raw in mail.store.sent: (OUT / "sent.eml").write_bytes(raw)
app.kill(); mail.close()
