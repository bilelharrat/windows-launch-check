"""Real-Windows check of the Windows-gap fixes, run with the installed app's bundled Python:

    "%LOCALAPPDATA%\\Programs\\Eden Code\\resources\\backend\\python\\python.exe" win_real_app.py

(Adjust the folder to where Eden Code is installed.) No network keys, no accounts, nothing of
yours is read: browsers are only looked for, never opened. The discovery check advertises
this PC on the local network for a few seconds. It prints PASS/FAIL per item; exit 1 on any FAIL.
Elsewhere than Windows it runs what it can as a smoke test and exits 3 (proves nothing).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
results: list[tuple[str, str, str]] = []


def item(name):
    def wrap(fn):
        try:
            detail = fn() or ""
            results.append((name, "PASS", str(detail)))
        except Skip as exc:
            results.append((name, "SKIP", str(exc)))
        except BaseException as exc:  # noqa: BLE001 - every item reports, whatever it raised
            results.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()
        print(f"{results[-1][1]}  {name}  {results[-1][2]}", flush=True)
        return fn

    return wrap


class Skip(Exception):
    pass


def windows_only():
    if not IS_WINDOWS:
        raise Skip("not Windows")


def new_loop():
    return asyncio.ProactorEventLoop() if IS_WINDOWS else asyncio.SelectorEventLoop()


@item("import eden_code")
def _import():
    import eden_code
    from eden_code import osplat

    assert osplat.IS_WIN == IS_WINDOWS
    return f"from {Path(eden_code.__file__).parent}, Python {sys.version.split()[0]}"


class Echo(asyncio.Protocol):
    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        self.transport.write(b"ok:" + data)
        self.transport.close()


def listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    sock.setblocking(False)
    return sock


@item("phone reader: the event loop is the one that refuses add_reader")
def _loop():
    windows_only()
    loop = new_loop()
    try:
        assert type(loop).__name__ == "ProactorEventLoop"
        left, right = socket.socketpair()
        try:
            try:
                loop.add_reader(left.fileno(), lambda: None)
            except NotImplementedError:
                return "ProactorEventLoop: add_reader raises NotImplementedError, as the audit said"
            raise AssertionError("add_reader worked: the fallback isn't what's running here")
        finally:
            left.close()
            right.close()
    finally:
        loop.close()


@item("phone reader: first byte peeked and left in place on a local socket")
def _first_byte():
    from eden_code import remote

    async def work():
        loop = asyncio.get_running_loop()
        server = listener()
        client = socket.create_connection(server.getsockname(), timeout=5)
        try:
            conn, _ = await asyncio.wait_for(loop.sock_accept(server), 5)
            conn.setblocking(False)
            waiting = asyncio.create_task(remote.Doorway._first_byte(loop, conn))
            await asyncio.sleep(0.05)
            client.sendall(b"Zrest")
            first = await asyncio.wait_for(waiting, 5)
            assert first == b"Z", first
            rest = await asyncio.wait_for(loop.sock_recv(conn, 16), 5)
            assert rest == b"Zrest", rest
            client.close()
            await asyncio.sleep(0.05)
            ended = await asyncio.wait_for(remote.Doorway._first_byte(loop, conn), 5)
            assert ended == b"", ended
            conn.close()
        finally:
            client.close()
            server.close()

    with asyncio.Runner(loop_factory=new_loop) as runner:
        runner.run(asyncio.wait_for(work(), 30))


@item("phone reader: a plain connection through the whole Doorway")
def _doorway_plain():
    from eden_code import remote

    async def work():
        server = listener()
        door = remote.Doorway(server, Echo, None, lambda: True)
        door.start()
        try:
            reader, writer = await asyncio.open_connection(*server.getsockname())
            writer.write(b"GET / HTTP/1.1\r\n\r\n")
            await writer.drain()
            answer = await asyncio.wait_for(reader.read(), 10)
            writer.close()
            assert answer == b"ok:GET / HTTP/1.1\r\n\r\n", answer
        finally:
            door.close()
            await door.wait_closed()
            server.close()

    with asyncio.Runner(loop_factory=new_loop) as runner:
        runner.run(asyncio.wait_for(work(), 30))


@item("phone reader: a TLS connection through the whole Doorway, with the app's own certificate code")
def _doorway_tls():
    from eden_code import companion_tls, remote

    async def work(folder):
        identity = companion_tls.load_or_create(Path(folder), ["127.0.0.1"])
        context = companion_tls.server_context(identity)
        server = listener()
        door = remote.Doorway(server, Echo, context, lambda: False)
        door.start()
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.check_hostname = False
        client.verify_mode = ssl.CERT_NONE
        try:
            reader, writer = await asyncio.open_connection(*server.getsockname(), ssl=client)
            writer.write(b"hello")
            await writer.drain()
            answer = await asyncio.wait_for(reader.read(), 10)
            writer.close()
            assert answer == b"ok:hello", answer
        finally:
            door.close()
            await door.wait_closed()
            server.close()

    with tempfile.TemporaryDirectory() as folder, asyncio.Runner(loop_factory=new_loop) as runner:
        runner.run(asyncio.wait_for(work(folder), 30))


@item("phone discovery: dnsapi accepts and confirms the advertisement")
def _discovery():
    windows_only()
    from eden_code import remote

    ad = remote.Advertiser()
    if not ad.start(remote.PORT, "", {"tls": "1", "fp": "windows-check"}):
        raise AssertionError(
            "not advertised (older than Windows 10 1809, or refused): the phone falls back to "
            "the QR code or the address"
        )
    try:
        if not ad._ref.done.wait(8):
            raise AssertionError("accepted, but Windows never said how it went")
        assert ad._ref.status == 0, f"Windows said status {ad._ref.status}"
    finally:
        ad.stop()
    return (
        "registered, then removed. Whether a phone sees it is NOT shown here: "
        f"check with a real phone (address {remote.addresses(remote.PORT)})"
    )


@item("container path conversion")
def _container_paths():
    from eden_code import devcontainer, osplat

    windows_only()
    assert osplat.IS_WIN
    for path, inside in (
        ("C:\\Users\\me\\app", "/c/Users/me/app"),
        ("D:\\w x\\app", "/d/w x/app"),
        ("\\\\nas\\share\\app", "/unc/nas/share/app"),
    ):
        got = devcontainer.container_path(path)
        assert got == inside, (path, got)
    here = Path(tempfile.gettempdir()).resolve()
    args = devcontainer.run_args("n", "s", "img", here, here)
    mount, work = args[args.index("-v") + 1], args[args.index("-w") + 1]
    assert work.startswith("/") and ":" not in work, work
    assert mount == f"{here}:{work}", mount
    docker = devcontainer.Docker()
    docker._path = "docker"
    assert docker.exec_argv("n", here, "ls")[3] == work
    return f"{here} -> {work}"


@item("container relay: a command through loopback, with its threads, no docker needed")
def _relay():
    from eden_code import container_run

    container_run.WINDOWS = True  # (already so on Windows; a smoke test elsewhere)
    script = (
        "import sys;sys.stdout.write(sys.argv[1]+'\\n');sys.stdout.flush();"
        "sys.stderr.write('E'*300000);sys.stderr.flush();sys.stdout.write('O'*300000);"
        "sys.stdout.flush();sys.exit(7)"
    )
    relay = container_run.Relay(lambda command: [sys.executable, "-c", script, command], lambda: None)
    try:
        path = relay.start()
        assert path.endswith(".addr"), path
        done = subprocess.run(
            [sys.executable, "-I", container_run.__file__, path, "--", "the command"],
            capture_output=True,
            timeout=60,
        )
        assert done.returncode == 7, (done.returncode, done.stderr[:300])
        assert done.stdout.replace(b"\r\n", b"\n").startswith(b"the command\n")
        assert done.stdout.count(b"O") == 300000 and done.stderr.count(b"E") == 300000
        port = json.loads(Path(path).read_text())["port"]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(b'{"command": "x"}\n')  # no token
            assert sock.recv(64)[:1] == b"x"
        slow = subprocess.Popen(
            [sys.executable, "-I", container_run.__file__, path, "--", "the command"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        slow.kill()
        slow.wait(10)
        slow.stdout.close()
    finally:
        relay.close()
    assert not Path(path).parent.exists(), "its folder stayed behind"
    return "status, both pipes and the refused token all as expected"


@item("browser import: Windows profile paths listed (nothing read)")
def _browser_paths():
    windows_only()
    from eden_code.features import browser_import as bi

    found = bi.sources(bi.home())
    ids = [s["id"] for s in found]
    assert ids == ["chrome", "edge", "brave", "firefox"], ids
    roots = {key: bi.chromium_root(key, bi.home()) for key in bi.WINDOWS_CHROMIUM}
    local = os.environ["LOCALAPPDATA"]
    for key, root in roots.items():
        assert str(root).lower().startswith(local.lower()) and str(root).endswith("User Data"), root
    return ", ".join(f"{s['id']}={'found' if s['found'] else 'none'}" for s in found)


SPI_SCRIPT = (
    "$t=Add-Type -Namespace Eden -Name Spi -PassThru -MemberDefinition '[DllImport(\"user32.dll\")] "
    "public static extern bool SystemParametersInfoW(uint a,uint b,ref int c,uint d);';"
    "$f=0;if($t::SystemParametersInfoW(0x46,0,[ref]$f,0)){if($f){'1'}else{'0'}}"
)


@item("screen-reader check: the backend's returns without error")
def _reader_backend():
    windows_only()
    from eden_code import osplat

    said = osplat.screen_reader_running()
    assert isinstance(said, bool), said
    return f"says {said}"


@item("screen-reader check: tasklist as app/a11y-detect.js runs it")
def _reader_tasklist():
    windows_only()
    root = os.environ.get("SystemRoot", "C:\\Windows")
    out = subprocess.run(
        [os.path.join(root, "System32", "tasklist.exe"), "/FO", "CSV", "/NH"],
        capture_output=True, timeout=10, check=True,
    ).stdout.decode(errors="replace")  # fmt: skip
    names = [line.split('","')[0].strip('"').lower() for line in out.splitlines() if line.startswith('"')]
    assert names, out[:300]
    readers = [n for n in names if n in ("nvda.exe", "jfw.exe", "narrator.exe", "supernova.exe", "zt.exe", "zoomtext.exe")]
    return f"{len(names)} programs; readers running: {readers or 'none'}"


@item("screen-reader check: SPI_GETSCREENREADER by PowerShell as app/a11y-detect.js runs it")
def _reader_spi():
    windows_only()
    root = os.environ.get("SystemRoot", "C:\\Windows")
    done = subprocess.run(
        [
            os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
            "-NoProfile", "-NonInteractive", "-EncodedCommand",
            base64.b64encode(SPI_SCRIPT.encode("utf-16-le")).decode(),
        ],
        capture_output=True, timeout=30,
    )  # fmt: skip
    said = done.stdout.decode(errors="replace").strip()
    assert said in ("0", "1"), (said, done.stderr.decode(errors="replace")[:300])
    return f"flag is {said} (with Narrator or NVDA running it should be 1)"


@item("look-at-this: the window in front and its selection read without error")
def _look():
    windows_only()
    from eden_code import codelook

    front = codelook.windows_front()
    assert isinstance(front, dict), front
    selected = codelook.windows_selected()
    assert isinstance(selected, str), selected
    return f"app {front.get('app')!r}, {len(selected)} characters selected (not shown)"


@item("On this PC: the list says what's still missing")
def _parity():
    windows_only()
    from eden_code import codeparity

    report = codeparity.report()
    ids = {row["id"]: row["state"] for row in report["features"]}
    assert report["os"] == "windows", report["os"]
    for name in ("sandbox", "secrets", "interpreter", "simulator", "passwords", "updates"):
        assert ids.get(name) == "mac", name
    return f"{len(ids)} rows"


failed = [r for r in results if r[1] == "FAIL"]
print(f"\n{sum(r[1] == 'PASS' for r in results)} passed, {len(failed)} failed, {sum(r[1] == 'SKIP' for r in results)} skipped")
if not IS_WINDOWS:
    print("Not Windows: this was only a smoke test of the script.")
sys.exit(1 if failed else 0 if IS_WINDOWS else 3)
