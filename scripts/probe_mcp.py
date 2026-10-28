"""Drive a built executable over stdio exactly as an MCP client would.

Run after packaging::

    python scripts/probe_mcp.py dist/软著申请材料生成器.exe

It performs the initialize handshake, lists the tools, calls one that needs no
arguments, and then runs a real generation through the MCP surface. A window
that opens proves nothing about whether an agent can use the program; this
does. Reads happen on a background thread because a blocked readline() on a
closed pipe is what made an earlier version of this probe hang forever.

Exit code is 0 only when every step passed.
"""

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

if len(sys.argv) < 2:
    print("usage: probe_mcp.py <path-to-exe>")
    raise SystemExit(2)

exe = Path(sys.argv[1])
proc = subprocess.Popen(
    [str(exe), "--mcp"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    bufsize=0,
)

lines: queue.Queue[bytes] = queue.Queue()
errs: list[bytes] = []


def read_stdout():
    for raw in proc.stdout:
        lines.put(raw)


def read_stderr():
    for raw in proc.stderr:
        errs.append(raw)


threading.Thread(target=read_stdout, daemon=True).start()
threading.Thread(target=read_stderr, daemon=True).start()


def send(msg):
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()


def wait_reply(id_, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = lines.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if obj.get("id") == id_:
            return obj
    return None


results = []

send(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2026-07-28",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1"},
        },
    }
)
init = wait_reply(1)
results.append(("initialize", init is not None, (init or {}).get("result", {}).get("serverInfo")))

send({"jsonrpc": "2.0", "method": "notifications/initialized"})

send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
tools = wait_reply(2)
names = [t["name"] for t in tools["result"]["tools"]] if tools else []
results.append(("tools/list", len(names), names))

send(
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "explain_rules", "arguments": {}},
    }
)
call = wait_reply(3)
text = ""
if call and "result" in call:
    content = call["result"].get("content", [])
    text = content[0].get("text", "") if content else ""
results.append(("tools/call explain_rules", bool(text), f"{len(text)} chars"))

# A real generation through the MCP surface, into a temp directory.
import tempfile

tmp = Path(tempfile.mkdtemp())
proj = tmp / "proj"
proj.mkdir()
for i in range(10):
    (proj / f"m{i}.ts").write_text(
        "\n".join(f"const v{i}_{n} = {n};" for n in range(300)) + "\n", encoding="utf-8"
    )

send(
    {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "generate_materials",
            "arguments": {
                "project_root": str(proj),
                "software_name": "MCP测试系统",
                "version": "V1.0",
                "output_dir": str(tmp / "out"),
                "fields": {
                    "owner": "测试公司",
                    "owner_category": "法人",
                    "development_completed": "2025-03-01",
                    "dev_os": "Windows 11",
                    "dev_ide": "VS Code",
                    "purpose": "测试",
                    "domain": "测试",
                    "main_functions": "本系统用于通过 MCP 接口生成软著材料，验证端到端可用性。" * 30,
                    "source_line_count": "3000",
                },
            },
        },
    }
)
gen = wait_reply(4, timeout=180)
if gen and "result" in gen:
    content = gen["result"].get("content", [])
    payload = content[0].get("text", "") if content else ""
    try:
        data = json.loads(payload)
        results.append(("tools/call generate_materials", data.get("ok"), data.get("rendered_pages")))
        pdfs = list((tmp / "out").rglob("*.pdf"))
        results.append(("PDF written", bool(pdfs), [p.name for p in pdfs]))
    except Exception as exc:
        results.append(("generate parse", False, str(exc)[:120]))
else:
    results.append(("tools/call generate_materials", False, gen))

proc.terminate()
time.sleep(0.5)

print("\n=== MCP over packaged executable ===")
ok = True
for name, passed, detail in results:
    print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")
    ok = ok and bool(passed)
if errs:
    print("stderr:", b"".join(errs).decode("utf-8", "replace")[:600])
print("RESULT:", "ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
