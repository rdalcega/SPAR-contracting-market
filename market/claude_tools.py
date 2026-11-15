"""The market's functions as an MCP server over stdio, for provider claude_code (market/claude_code.py).

Claude Code starts this script as the session's only tool server: `python -I claude_tools.py TOOLS SOCKET`. TOOLS is
a JSON file of the offered functions ({"name", "description", "inputSchema"}). A call is not run here: it waits
until the market's answer for its tool-use id arrives on the Unix socket SOCKET (one JSON line {"id", "text"} per
answer, in any order relative to the calls), and that answer becomes the call's result. Standard library only, and
nothing of the market is imported: the script runs in Claude Code's process tree.
"""

import json
import socket
import sys
import threading

ID = "claudecode/toolUseId"  # where Claude Code puts a call's tool-use id (params._meta)


def main(tools_path, socket_path):
    with open(tools_path, encoding="utf-8") as f:
        tools = json.load(f)
    lock = threading.Lock()
    waiting, answers = {}, {}  # tool-use id -> the call's JSON-RPC id / the market's answer

    def send(message):
        with lock:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()

    def settle(use_id):
        """Answer a call once both it and its answer are here (the caller holds `lock`)."""
        if use_id in waiting and use_id in answers:
            rid, text = waiting.pop(use_id), answers.pop(use_id)
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "result": {"content": [{"type": "text", "text": text}]}}) + "\n")
            sys.stdout.flush()

    def listen():
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(socket_path)
        for line in conn.makefile(encoding="utf-8"):
            answer = json.loads(line)
            with lock:
                answers[answer["id"]] = answer["text"]
                settle(answer["id"])

    threading.Thread(target=listen, daemon=True).start()
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:  # a notification
            continue
        method, params = message.get("method"), message.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                      "serverInfo": {"name": "market", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": tools}
        elif method == "tools/call":
            use_id = (params.get("_meta") or {}).get(ID)
            if use_id is None:
                result = {"isError": True, "content": [{"type": "text", "text": "a call without a tool-use id"}]}
            else:
                with lock:
                    waiting[use_id] = message["id"]
                    settle(use_id)
                continue
        else:
            result = {}
        send({"jsonrpc": "2.0", "id": message["id"], "result": result})


if __name__ == "__main__":
    main(*sys.argv[1:3])
