"""Eden Code's agent-session engine, checked for real on a real Windows PC.

Run it with the installed app's own Python (default per-user install; adjust if you changed it):

  "%LOCALAPPDATA%\\Programs\\Eden Code\\resources\\backend\\python\\python.exe" win_real_engine.py

It starts real processes: Git Bash, git, process trees that are killed, a fake npx and a fake claude
written to a temp folder (a copy of this Python plays node.exe, so Node needn't be installed). It uses
no network, no keys and no real accounts, and it kills only processes it started itself.
PASS/FAIL per item; exit code 1 on any FAIL.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

HOSTILE = 'line one\nline "two" & echo PWNED>pwned.txt | %PATH% ^ !x! (y) {"json": ["a b", "c\\\\"]}'
ITEM_SECONDS = 90.0
HANG_LIMIT = 25.0  # a call given 3 s may take this long in all, or it's counted as a hang

GRANDCHILD = """
import os, sys, time
open(sys.argv[1], 'w').write(str(os.getpid()))
print('grandchild holding the pipe', flush=True)
time.sleep(300)
"""
# Starts the grandchild with our stdout, so it holds the same pipe, then waits on it.
PARENT = """
import os, subprocess, sys, time
open(sys.argv[1], 'w').write(str(os.getpid()))
subprocess.Popen([sys.executable, sys.argv[3], sys.argv[2]], stdout=sys.stdout.fileno(), stderr=subprocess.STDOUT)
time.sleep(300)
"""
FAKE_MCP = """
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if 'id' not in m:
        continue
    r = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fake', 'version': '1'}}
    if m['method'] == 'tools/list':
        r = {'tools': [{'name': 'fake_tool', 'description': json.dumps(sys.argv[1:]), 'inputSchema': {'type': 'object'}}]}
    print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': r}), flush=True)
"""
FAKE_CLAUDE = """
import json, os, sys
json.dump({'argv': sys.argv[1:], 'key': 'ANTHROPIC_API_KEY' in os.environ}, open(os.environ['FAKE_CLAUDE_OUT'], 'w'))
"""
NPM_SHIM = (
    '@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\nCALL :find_dp0\r\n\r\n'
    'IF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n  SET "_prog=node"\r\n)\r\n\r\n'
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\{target}" %*\r\n'
)
NODE_NPX = (
    '@ECHO OFF\r\nSETLOCAL\r\nSET "NODE_EXE=%~dp0\\node.exe"\r\n'
    'SET "NPX_CLI_JS=%~dp0\\node_modules\\npm\\bin\\npx-cli.js"\r\n"%NODE_EXE%" "%NPX_CLI_JS%" %*\r\n'
)
EXE_SUFFIX = ".exe"


def shell_path(path: Path | str) -> str:
    """A path as bash reads it, quoted."""
    return shlex.quote(str(path).replace("\\", "/"))


def make_node(folder: Path) -> Path:
    """A node.exe that's really this Python (its PYTHONHOME says where its library is): it runs
    whichever .js file it's given, and each of those here is Python."""
    folder.mkdir(parents=True, exist_ok=True)
    node = folder / "node.exe"
    shutil.copy2(sys.executable, node)
    for dll in Path(sys.executable).parent.glob("*.dll"):
        shutil.copy2(dll, folder / dll.name)
    return node


def pid_of(path: Path, wait: float = 20.0) -> int:
    end = time.monotonic() + wait
    while time.monotonic() < end:
        with contextlib.suppress(OSError, ValueError):
            text = path.read_text().strip()
            if text:
                return int(text)
        time.sleep(0.05)
    raise AssertionError(f"{path.name} never appeared: the process tree didn't start")


def alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def tidy(pids: list[int]) -> None:
    import psutil

    for pid in pids:
        with contextlib.suppress(psutil.Error):
            psutil.Process(pid).kill()


class Checks:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.leftover: list[int] = []
        (tmp / "grandchild.py").write_text(GRANDCHILD)
        (tmp / "parent.py").write_text(PARENT)

    def tree_command(self, tag: str) -> tuple[str, Path, Path]:
        """bash -> python -> python, the last two sharing the call's pipe."""
        parent, grand = self.tmp / f"{tag}-parent.pid", self.tmp / f"{tag}-grand.pid"
        py = shell_path(sys.executable)
        line = " ".join([py, shell_path(self.tmp / "parent.py"), shell_path(parent), shell_path(grand), shell_path(self.tmp / "grandchild.py")])
        return line, parent, grand

    def assert_dead(self, *files: Path) -> None:
        pids = [pid_of(f) for f in files]
        self.leftover += pids
        end = time.monotonic() + 8
        while time.monotonic() < end and any(alive(p) for p in pids):
            time.sleep(0.1)
        still = [p for p in pids if alive(p)]
        assert not still, f"orphans still running: {still}"

    async def bash_tool(self) -> str:
        from eden_code.engine.tools import ToolExecutor

        out, err = await ToolExecutor(self.tmp).execute(
            "Bash", {"command": "echo hello-$((40+2)); uname -s; sleep 0; tar --version >/dev/null; cat <(echo substituted) | tr a-z A-Z"}
        )
        assert not err, out
        assert "hello-42" in out and "SUBSTITUTED" in out, out
        return " ".join(out.split())[:80]

    async def cd_persists(self) -> str:
        from eden_code.engine.tools import ToolExecutor

        (self.tmp / "sub dir").mkdir(exist_ok=True)
        runner = ToolExecutor(self.tmp)
        out, err = await runner.execute("Bash", {"command": 'cd "sub dir" && pwd'})
        assert not err, out
        assert Path(runner.cwd).name == "sub dir", f"cwd is {runner.cwd}"
        return str(runner.cwd)

    async def bundled_git(self) -> str:
        from eden_code import osplat

        root = osplat.bundled_git_root()
        assert root is not None, "no bundled Git in this install: resources/backend/git is missing"
        assert (root / "LICENSE.txt").is_file() and (root / "NOTICE.txt").is_file(), "the licence isn't beside it"
        git = str(root / "cmd" / "git.exe")
        version = subprocess.run([git, "--version"], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        repo = self.tmp / "repo"
        repo.mkdir()
        (repo / "a.txt").write_text("hi\n")
        for args in (["init", "-q"], ["add", "."], ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "m"]):
            subprocess.run([git, *args], cwd=repo, capture_output=True, text=True, timeout=60, check=True)
        log = subprocess.run([git, "log", "--oneline"], cwd=repo, capture_output=True, text=True, timeout=30, check=True).stdout
        assert " m" in log, log
        from eden_code.engine.tools import ToolExecutor

        # In a session: bash finds its own git, and it's the same bundle.
        out, err = await ToolExecutor(repo).execute("Bash", {"command": "git log --oneline | wc -l; git --exec-path; which sleep tar sed grep awk perl nano"})
        assert not err and out.split()[0] == "1", out
        return version

    def in_use(self) -> str:
        from eden_code import osplat
        from eden_code.features.ops import doctor

        check = doctor.git_check()
        assert check["state"] == "ok", check
        return f"{check['summary']}: {check['meta']} (git: {osplat.git_in_use()['git']})"

    async def timeout_kills_tree(self) -> str:
        from eden_code.engine.tools import ToolExecutor

        line, parent, grand = self.tree_command("timeout")
        started = time.monotonic()
        out, err = await asyncio.wait_for(ToolExecutor(self.tmp).execute("Bash", {"command": line, "timeout": 3000}), HANG_LIMIT)
        took = time.monotonic() - started
        assert err and "timed out" in out, out
        self.assert_dead(parent, grand)
        return f"returned in {took:.1f} s, tree gone"

    async def orphan_killed(self) -> str:
        """The grandchild's parent has already gone (taskkill /T can't reach it): the job must."""
        from eden_code.engine.tools import ToolExecutor

        grand = self.tmp / "orphan-grand.pid"
        line = f"({shell_path(sys.executable)} {shell_path(self.tmp / 'grandchild.py')} {shell_path(grand)} &) ; sleep 300"
        started = time.monotonic()
        out, err = await asyncio.wait_for(ToolExecutor(self.tmp).execute("Bash", {"command": line, "timeout": 3000}), HANG_LIMIT)
        took = time.monotonic() - started
        assert err and "timed out" in out, out
        self.assert_dead(grand)
        return f"returned in {took:.1f} s, orphan gone"

    async def stop_kills_tree(self) -> str:
        from eden_code.engine.tools import ToolExecutor

        line, parent, grand = self.tree_command("stop")
        runner = ToolExecutor(self.tmp)
        out, err = await runner.execute("Bash", {"command": line, "run_in_background": True}, "job-1")
        assert not err, out
        await asyncio.to_thread(pid_of, grand)
        started = time.monotonic()
        await asyncio.wait_for(runner.stop_background_job("job-1"), HANG_LIMIT)
        took = time.monotonic() - started
        assert runner.background_jobs["job-1"].status == "stopped"
        self.assert_dead(parent, grand)
        return f"stopped in {took:.1f} s, tree gone"

    async def cancel_kills_tree(self) -> str:
        """Stop pressed while a command runs in the foreground: the turn's task is cancelled."""
        from eden_code.engine.tools import ToolExecutor

        line, parent, grand = self.tree_command("cancel")
        call = asyncio.ensure_future(ToolExecutor(self.tmp).execute("Bash", {"command": line, "timeout": 120000}))
        await asyncio.to_thread(pid_of, grand)
        started = time.monotonic()
        call.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(call, HANG_LIMIT)
        took = time.monotonic() - started
        self.assert_dead(parent, grand)
        return f"cancelled in {took:.1f} s, tree gone"

    def fake_node_folder(self, name: str) -> tuple[Path, Path, dict[str, str]]:
        folder = self.tmp / name
        node = make_node(folder)
        env = {"PYTHONHOME": sys.base_prefix}
        return folder, node, env

    async def npx_stdio_server(self) -> str:
        from eden_code import osplat
        from eden_code.engine import mcp_client

        folder, node, env = self.fake_node_folder("nodejs")
        js = folder / "node_modules" / "npm" / "bin" / "npx-cli.js"
        js.parent.mkdir(parents=True)
        js.write_text(FAKE_MCP)
        (folder / "npx.cmd").write_text(NODE_NPX, newline="")
        path = os.pathsep.join([str(folder), os.environ.get("PATH", "")])
        resolved = osplat.resolve_command(["npx", "-y", "fake-server"], {**os.environ, "PATH": path})
        assert resolved[:2] == [str(node), str(js)], resolved
        manager = mcp_client.McpManager(None, self.tmp)
        tricky = 'a b "c" & echo PWNED>pwned.txt'
        srv = mcp_client.ConnectedMcpServer(
            name="fake", config={"command": "npx", "args": ["-y", "fake-server", tricky], "env": {"PATH": path, **env}}
        )
        try:
            await manager._connect_stdio_server(srv)
            assert srv.status == "connected", f"{srv.status}: {srv.error}"
            said = json.loads(srv.tools[0]["description"])
            assert said == ["-y", "fake-server", tricky], said
            assert not list(self.tmp.rglob("pwned.txt")), "cmd.exe ran part of an argument"
        finally:
            await manager._close_server_process(srv)
        real = shutil.which("npx")
        if real:
            done = subprocess.run(osplat.resolve_command(["npx", "--version"]), capture_output=True, text=True, timeout=60, check=False)
            assert done.returncode == 0, done.stderr
            return f"fake server connected; real npx {done.stdout.strip()} ran through the resolver"
        return "fake server connected (no real npx on this PC to try as well)"

    async def claude_launcher(self) -> str:
        from eden_code import osplat
        from eden_code.engine.cli_client import ClaudeCodeCliClient
        from eden_code.engine.types import ClaudeAgentOptions

        folder, node, env = self.fake_node_folder("npm")
        js = folder / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        js.parent.mkdir(parents=True)
        js.write_text(FAKE_CLAUDE)
        shim = folder / "claude.cmd"
        shim.write_text(NPM_SHIM.replace("{target}", r"node_modules\@anthropic-ai\claude-code\cli.js"), newline="")
        out_file = self.tmp / "claude-args.json"
        schema = {"type": "object", "properties": {"a": {"const": 'x "y" & z'}}}
        options = ClaudeAgentOptions(
            system_prompt=HOSTILE, cwd=str(self.tmp), output_format={"type": "json_schema", "schema": schema}
        )
        base = {**os.environ, **env, "FAKE_CLAUDE_OUT": str(out_file), "ANTHROPIC_API_KEY": "sentinel-not-a-key"}
        client = ClaudeCodeCliClient(options, cli=str(shim), base_env=base)
        cmd = client.command()
        resolved = osplat.resolve_command(cmd, cwd=str(self.tmp))
        assert resolved[:2] == [str(node), str(js)], resolved[:3]
        assert not any(str(a).lower().endswith("cmd.exe") for a in resolved)
        proc = await asyncio.create_subprocess_exec(*resolved, cwd=str(self.tmp), env=client.environment(), stdin=asyncio.subprocess.DEVNULL)
        assert await asyncio.wait_for(proc.wait(), 60) == 0
        got = json.loads(out_file.read_text())
        assert got["argv"] == resolved[2:], "an argument changed on the way"
        assert got["argv"][got["argv"].index("--system-prompt") + 1] == HOSTILE
        assert json.loads(got["argv"][got["argv"].index("--json-schema") + 1]) == schema
        assert got["key"] is False, "an API key reached the Claude CLI"
        assert not list(self.tmp.rglob("pwned.txt")), "cmd.exe ran part of an argument"
        return "multi-line and JSON arguments came through unchanged, no key passed"

    def plain_batch(self) -> str:
        from eden_code import osplat

        folder = self.tmp / "plain"
        folder.mkdir()
        (folder / "mine.cmd").write_text("@echo off\r\necho got %1\r\n", newline="")
        env = {**os.environ, "PATH": str(folder) + os.pathsep + os.environ.get("PATH", "")}
        argv = osplat.resolve_command(["mine", "safe-word"], env)
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=True).stdout
        assert "got safe-word" in out, out
        try:
            osplat.resolve_command(["mine", 'x" & echo PWNED>pwned.txt'], env)
        except osplat.BatchArgsRefused:
            return "safe words ran, a hostile one was refused"
        raise AssertionError("a hostile argument wasn't refused")

    async def hooks(self) -> str:
        from eden_code.engine import claude_config as cc

        code, out, err = await cc._run_one(
            cc.HookCommand('test -n "$CLAUDE_PROJECT_DIR" && cat >/dev/null && echo hook-$((1+1))'), {"a": 1}, self.tmp, {}
        )
        assert code == 0 and "hook-2" in out, (code, out, err)
        line, parent, grand = self.tree_command("hook")
        started = time.monotonic()
        code, _out, err = await asyncio.wait_for(cc._run_one(cc.HookCommand(line, timeout=3.0), {}, self.tmp, {}), HANG_LIMIT)
        took = time.monotonic() - started
        assert code == 1 and "timed out" in err, (code, err)
        self.assert_dead(parent, grand)
        return f"bash-style hook ran; timed-out hook returned in {took:.1f} s, tree gone"

    def prompts(self) -> str:
        from eden_code import tasks
        from eden_code.engine import cli_client, client

        for name, text in (
            ("system prompt", client.EDEN_CODE_SYSTEM_PROMPT),
            ("identity", tasks.EDEN_CODE_IDENTITY),
            ("auto check", tasks.AUTO_SYSTEM),
        ):
            assert "this Mac" not in text and "person's Mac" not in text, f"{name} says Mac"
            assert "Windows" in text and "Git Bash" in text, f"{name} doesn't say Windows"
        assert "this Mac" not in cli_client.MISSING_WORDS
        return "the model is told Windows, Git Bash and the path style"


async def run_all(checks: Checks) -> list[tuple[str, bool, str]]:
    items = [
        ("bash tool runs a command", checks.bash_tool),
        ("bash tool keeps a cd (path with a space)", checks.cd_persists),
        ("bundled git works, licence beside it", checks.bundled_git),
        ("checkup says which git is in use", checks.in_use),
        ("timeout kills tree, grandchild holding pipe: no hang", checks.timeout_kills_tree),
        ("timeout kills an orphaned grandchild", checks.orphan_killed),
        ("Stop (background) kills tree", checks.stop_kills_tree),
        ("Stop (foreground, turn cancelled) kills tree", checks.cancel_kills_tree),
        ("npx .cmd stdio server launches via resolver", checks.npx_stdio_server),
        ("claude launcher: multi-line + JSON via fake claude.cmd", checks.claude_launcher),
        ("other batch files: safe args only", checks.plain_batch),
        ("hooks run in bash, time-out kills tree", checks.hooks),
        ("model is told the true OS", checks.prompts),
    ]
    results = []
    for name, check in items:
        try:
            said = check()
            if asyncio.iscoroutine(said):
                said = await asyncio.wait_for(said, ITEM_SECONDS)
            results.append((name, True, str(said)))
        except BaseException as exc:  # noqa: BLE001 - every item is reported, whatever it did
            if isinstance(exc, KeyboardInterrupt):
                raise
            detail = str(exc) or traceback.format_exc(limit=3)
            results.append((name, False, f"{type(exc).__name__}: {detail}"))
        ok, words = results[-1][1:]
        print(f"{'PASS' if ok else 'FAIL'}  {name}\n      {words}", flush=True)
    return results


def main() -> int:
    if sys.platform != "win32":
        print("This checks Eden Code on real Windows: run it there, with the installed app's Python.")
        return 2
    print(f"Python: {sys.executable}")
    try:
        import eden_code

        print(f"eden_code: {Path(eden_code.__file__).parent}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  import eden_code\n      {exc}")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="eden win check "))  # (a space on purpose)
    checks = Checks(tmp)
    try:
        results = asyncio.run(run_all(checks))
    finally:
        tidy(checks.leftover)
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
