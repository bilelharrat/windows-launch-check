"""Records J.A.R.V.I.S. Daredevil on this Windows machine for the askeden.com video: the real app, real
replies from Claude, the camera fed a photo of a letter. The machine has no microphone or speakers, so the
requests are typed on screen and Jarvis's own Windows voice is laid over afterwards from its real replies,
at the moments it gave them. Writes out/raw.mp4, out/final.mp4, out/captions.vtt, out/timeline.json."""
import json, os, subprocess, sys, time, urllib.request, wave
from pathlib import Path
import websocket

EXE = Path(sys.argv[1]); OUT = Path("out"); OUT.mkdir(exist_ok=True)
VIDEO = Path("video"); PORT = 9333
SCENES = [
    ("Find the three most cited papers on sleep and memory since 2015.", 150),
    ("Look through my camera and read me this parking notice.", 150),
]
timeline = []
t0 = None
def now(): return time.time() - t0
def note(kind, text): timeline.append({"t": round(now(), 2), "kind": kind, "text": text}); print(f"{now():6.1f}s {kind}: {text[:160]}", flush=True)

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
    def key(self, key, code, vk, text=""):
        for t in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, key=key, code=code, windowsVirtualKeyCode=vk, **({"text": text} if text and t == "keyDown" else {}))

def targets():
    try: return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3))
    except Exception: return []

# the camera: the letter as a 20-second webcam feed
y4m = OUT / "letter.y4m"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(VIDEO / "letter.png"), "-t", "20", "-r", "15", "-vf", "scale=1280:720,format=yuv420p", str(y4m)], check=True)

# start recording the screen, then the app
rec = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "gdigrab", "-framerate", "30", "-i", "desktop", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(OUT / "raw.mp4")], stdin=subprocess.PIPE)
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
# the whole screen for the window (the browser's own command; events can arrive before its answer)
def browser_call(ws, n, method, **params):
    ws.send(json.dumps({"id": n, "method": method, "params": params}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == n: return m
try:
    ver = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=3))
    br = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
    tid = [t for t in targets() if t.get("type") == "page" and t.get("url", "").startswith("http://127.0.0.1")][0]["id"]
    w = browser_call(br, 1, "Browser.getWindowForTarget", targetId=tid)["result"]["windowId"]
    print("normal:", browser_call(br, 2, "Browser.setWindowBounds", windowId=w, bounds={"windowState": "normal"}))
    print("size:", browser_call(br, 3, "Browser.setWindowBounds", windowId=w, bounds={"left": 0, "top": 0, "width": 1920, "height": 1080}))
except Exception as e: print("maximize:", e)
# full screen through Windows itself: no frame, over the taskbar, the whole display
try:
    import ctypes
    u = ctypes.windll.user32
    h = 0
    for _ in range(20):
        h = u.FindWindowW(None, "J.A.R.V.I.S. Daredevil") or u.FindWindowW(None, "Jarvis")
        if h: break
        time.sleep(0.5)
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    u.SetWindowLongW(h, -16, 0x80000000 | 0x10000000)  # WS_POPUP | WS_VISIBLE
    u.SetWindowPos(h, -1, 0, 0, sw, sh, 0x0020 | 0x0040)  # topmost, frame changed, shown
    class R(ctypes.Structure): _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    r = R(); u.GetWindowRect(h, ctypes.byref(r)); print("window rect:", r.l, r.t, r.r, r.b)
    print("full screen:", h, sw, sh)
    try:
        print("size again:", browser_call(br, 4, "Browser.setWindowBounds", windowId=w, bounds={"left": 0, "top": 0, "width": sw, "height": sh}))
        u.GetWindowRect(h, ctypes.byref(r)); print("window rect now:", r.l, r.t, r.r, r.b)
    except Exception as e: print("size again:", e)
except Exception as e: print("full screen:", e)
time.sleep(3)
# Claude: the owner's key, given the way the app's own setup gives it (never shown on screen)
key = os.environ.get("ANTHROPIC_API_KEY", "")
page.js(f"send({{type: 'signin_key', key: {json.dumps(key)}}}); true")
time.sleep(8)
for _ in range(3): page.key("Escape", "Escape", 27); time.sleep(0.4)  # (the first-run setup: not in this video)
time.sleep(2)
seen = len(page.js("window.jarvisAccessibility ? window.jarvisAccessibility.state().spoken : []") or [])

for request, limit in SCENES:
    page.js("(() => { const b = document.getElementById('ask-input'); if (b) { b.focus(); b.value=''; } return !!b; })()")
    time.sleep(1)
    note("you", request)
    for ch in request:
        page.call("Input.insertText", text=ch); time.sleep(0.045)
    time.sleep(0.6); page.key("Enter", "Enter", 13, "\r")
    started = time.time(); prev = ""; changed = time.time(); first = None
    before = page.js("(document.getElementById('reply') || {}).innerText || ''") or ""
    while time.time() - started < limit:
        text = page.js("(document.getElementById('reply') || {}).innerText || ''") or ""
        state = page.js("document.body.dataset.state || ''")
        if text and text != before and text != prev:
            prev, changed = text, time.time()
            if first is None: first = now()
        if prev and state == "idle" and time.time() - changed > 6 and time.time() - started > 15: break
        time.sleep(0.5)
    if prev:
        timeline.append({"t": round(first, 2), "kind": "jarvis", "text": " ".join(prev.split())})
        print(f"{first:6.1f}s jarvis: {prev[:300]}", flush=True)
    time.sleep(3)

time.sleep(2); total = now()
rec.communicate(b"q", timeout=60)
(OUT / "timeline.json").write_text(json.dumps(timeline, indent=1))
print("recorded", round(total, 1), "s")

# Jarvis's own Windows voice over its real replies, at the moments it gave them
py = EXE.parent / "resources" / "backend" / "python" / "python.exe"
clips = [(0.3, str(EXE.parent / "resources" / "app.asar.unpacked" / "daredevil-open.wav"))] if (EXE.parent / "resources" / "app.asar.unpacked" / "daredevil-open.wav").exists() else []
open_wav = VIDEO / "daredevil-open.wav"
if not clips and open_wav.exists(): clips = [(next(e["t"] for e in timeline if e["kind"] == "open") + 1.5, str(open_wav))]
for i, e in enumerate(x for x in timeline if x["kind"] == "jarvis"):
    wav = OUT / f"say{i:02d}.wav"
    subprocess.run([str(py), "-I", "-m", "jarvis.winsay", "-r", "200", "-o", str(wav), "--data-format=LEI16@22050"], input=e["text"].encode("utf-8"), check=False)
    if wav.exists() and wav.stat().st_size > 1000: clips.append((e["t"], str(wav)))
inputs, filt = [], []
for i, (t, f) in enumerate(clips):
    inputs += ["-i", f]; filt.append(f"[{i+1}:a]adelay={int(t*1000)}|{int(t*1000)},aresample=44100,aformat=channel_layouts=stereo[a{i}]")
mix = "".join(f"[a{i}]" for i in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0:duration=longest[aout]"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(OUT / "raw.mp4"), *inputs, "-filter_complex", ";".join(filt + [mix]), "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(OUT / "final.mp4")], check=True)

# captions (WebVTT): what was typed, and what Jarvis said, each for as long as it was being said
def ts(s): h, r = divmod(max(0, s), 3600); m, sec = divmod(r, 60); return f"{int(h):02d}:{int(m):02d}:{sec:06.3f}"
cues = []
for e in timeline:
    if e["kind"] not in ("you", "jarvis"): continue
    words = len(e["text"].split()); dur = max(2.5, words / 3.2)
    cues.append(f"{ts(e['t'])} --> {ts(e['t'] + dur)}\n{'You: ' if e['kind'] == 'you' else 'Jarvis: '}{e['text']}")
(OUT / "captions.vtt").write_text("WEBVTT\n\n" + "\n\n".join(cues) + "\n", encoding="utf-8")
app.kill()
