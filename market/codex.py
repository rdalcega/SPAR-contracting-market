"""Provider codex: model calls answered by Codex (`codex app-server`) on a ChatGPT subscription. How a conversation
maps onto a session, and what any harness provider does differently: market/live.py.

Each session is one `codex app-server` process (its own process group) holding one ephemeral thread. The market's
functions are the thread's dynamic tools: Codex hands each call to this client, which answers it with the market's
tool message. The thread's base instructions are the system message; each user message starts a turn.

Login: the user's own Codex login (`codex login`), read from CODEX_HOME/auth.json (default ~/.codex) and given to the
session as an access token held in memory ("chatgptAuthTokens"). The file is only read: its refresh token, which
rotates, never leaves it. When Codex asks for a fresh token the file is read again. Nothing here refreshes the login:
the user's own Codex does when it runs (its access token lasts about a week), so a run must end before the token
expires; setup refuses one that expires within SETUP_MARGIN.

Isolation: each session has its own empty CODEX_HOME and working folder, an environment of PATH, HOME and CODEX_HOME
only, and the switches of _OFF and _CONFIG: no shell, patch, search, web, image, plan, multi-agent, skill, plugin,
app, hook or MCP tool, no AGENTS.md, no permissions, environment, apps, skills or collaboration instructions, and a
model catalog (built from Codex's own at setup) whose models take functions directly rather than through code mode.
A thread that reports loaded instruction files is not used. Measured on 2026-11-16 (codex-cli 0.163.0): the model
then receives the base instructions, the view and the two functions under their own names, and nothing else.

What still differs from the OpenAI-compatible providers:
- max_tokens is not applied: Codex has no setting for a model's output limit, so no reply is cut at it;
- reasoning is the summary Codex returns (summary "detailed"), never the raw reasoning;
- calls are one per response (Codex sends parallel_tool_calls false);
- the finish reason is "tool_calls" or "stop": Codex does not say why a response ended, so a reply cut or filtered
  is not reported as "length" or "content_filter";
- input tokens count cached tokens as plain input, at the spec's input price; a request Codex makes with no model
  output (warming its connection) is not counted.
"""

import base64
import functools
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque

from . import live
from .models import SetupError

CLI = "codex"
_OFF = ("shell_tool", "unified_exec", "view_image", "image_generation", "apps", "plugins", "remote_plugin",
        "multi_agent", "goals", "sleep_tool", "tool_suggest", "skill_search", "browser_use", "browser_use_external",
        "computer_use", "in_app_browser", "hooks", "skill_mcp_dependency_install", "tool_call_mcp_elicitation",
        "realtime_conversation", "shell_snapshot", "daemon_auto_start", "enable_request_compression",
        "workspace_dependencies", "worktrees", "code_mode_host")  # features turned off
_CONFIG = {"project_doc_max_bytes": 0, "include_permissions_instructions": False,
           "include_environment_context": False, "include_apps_instructions": False,
           "include_collaboration_mode_instructions": False, "skills.include_instructions": False,
           "tools.experimental_request_user_input.enabled": False, "tools.update_plan.enabled": False,
           "web_search": "disabled"}
_DIRECT = {"tool_mode": "direct_model_only", "apply_patch_tool_type": None, "supports_search_tool": False,
           "multi_agent_version": None}  # each catalog model: functions taken directly, no other tools
_LIMITED = ("usageLimitExceeded", "rateLimitExceeded")
SETUP_MARGIN = 6 * 3600  # seconds: the least a login must have left when a backend is set up


def backend(spec, llm, tracker, *, cli=None, login_home=None, sleep=time.sleep, clock=time.time):
    """models.md 3, "Setting up": the backend of a codex spec; SetupError when Codex is missing, the user is not
    logged in to ChatGPT, or Codex does not know the model or its effort."""
    cli = cli or CLI
    if shutil.which(cli) is None:
        raise SetupError(f"provider codex needs Codex's `{cli}` command on PATH")
    login_home = login_home or os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    tokens(login_home, clock, SETUP_MARGIN)
    return _Backend(spec, llm, tracker, cli, login_home, catalog(cli, spec), sleep, clock)


def tokens(login_home, clock=time.time, margin=60):
    """The access token and account id of the user's ChatGPT login; SetupError when there is none, or it expires
    within `margin` seconds."""
    path = os.path.join(login_home, "auth.json")
    try:
        with open(path, encoding="utf-8") as f:
            auth = json.load(f)
        found = auth["tokens"]
        access, account = found["access_token"], found["account_id"]
        if auth.get("auth_mode", "chatgpt") != "chatgpt" or not isinstance(access, str) or not isinstance(account, str):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise SetupError(f"provider codex needs a ChatGPT login: run `codex login` ({path})") from None
    try:
        claims = access.split(".")[1]
        expires = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))["exp"]
    except (IndexError, ValueError, KeyError, TypeError):
        expires = None
    if isinstance(expires, (int, float)) and expires <= clock() + margin:
        raise SetupError("the ChatGPT login of provider codex expires within "
                         f"{margin // 3600 or 1} h: refresh it with `codex login`")
    return access, account


def catalog(cli, spec):
    """Codex's model catalog with every model taking functions directly (_DIRECT), as JSON text; SetupError when it
    cannot be read or lacks the spec's model or effort."""
    models = json.loads(_models(cli))
    entry = next((m for m in models if isinstance(m, dict) and m.get("slug") == spec["model"]), None)
    if entry is None:
        known = ", ".join(str(m.get("slug")) for m in models if isinstance(m, dict))
        raise SetupError(f"Codex has no model '{spec['model']}' (it has: {known})")
    effort = (spec["extra_body"].get("reasoning") or {}).get("effort")
    levels = [x.get("effort") if isinstance(x, dict) else x for x in entry.get("supported_reasoning_levels") or []]
    if effort is not None and effort not in levels:
        raise SetupError(f"Codex's model '{spec['model']}' takes no reasoning effort '{effort}' "
                         f"(it takes: {', '.join(map(str, levels))})")
    for m in models:
        if isinstance(m, dict):
            m.update(_DIRECT)
    return json.dumps({"models": models})


@functools.cache
def _models(cli):
    """Codex's model list as `codex debug models` prints it in an empty home, as JSON text; read once a process."""
    home = tempfile.mkdtemp(prefix="cxc-")
    try:
        done = subprocess.run([cli, "debug", "models"], capture_output=True, text=True, timeout=120,
                              env={"PATH": os.environ.get("PATH", ""), "HOME": home, "CODEX_HOME": home})
        models = json.loads(done.stdout)["models"]
        if not isinstance(models, list):
            raise TypeError("the models are not a list")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
        raise SetupError(f"provider codex could not read Codex's model catalog: {e}") from None
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return json.dumps(models)


class _Session(live.Session):
    """One `codex app-server` process with one thread."""

    def launch(self, system, functions, deadline):
        owner = self.owner
        self.backlog, self.requests, self.replies = deque(), {}, {}
        self.n, self.limits, self.thread = 0, None, None
        self.dir = tempfile.mkdtemp(prefix="cx-")
        home, work = os.path.join(self.dir, "home"), os.path.join(self.dir, "work")
        os.mkdir(home)
        os.mkdir(work)
        catalog_file = os.path.join(self.dir, "catalog.json")
        with open(catalog_file, "w", encoding="utf-8") as f:
            f.write(owner.catalog)
        args = [owner.cli, "app-server"]
        for feature in _OFF:
            args += ["--disable", feature]
        for key, value in {**_CONFIG, "model_catalog_json": catalog_file}.items():
            args += ["-c", f"{key}={json.dumps(value)}"]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": home, "CODEX_HOME": home}
        self.stderr = os.path.join(self.dir, "stderr.txt")
        try:
            with open(self.stderr, "w") as err:
                self.proc = subprocess.Popen(args, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=err, text=True, encoding="utf-8", start_new_session=True)
        except OSError as e:
            raise live.Lost(f"Codex could not be started: {e}") from None
        threading.Thread(target=self.pump, daemon=True).start()
        self._request("initialize", {"clientInfo": {"name": "contracting-market", "title": None, "version": "1"},
                                     "capabilities": {"experimentalApi": True}}, deadline)
        self._send({"method": "initialized"}, deadline)
        self._login("account/login/start", deadline)
        started = self._request("thread/start", {
            "model": owner.model, "cwd": work, "approvalPolicy": "never", "sandbox": "read-only",
            "baseInstructions": system, "ephemeral": True, "experimentalRawEvents": True,
            "dynamicTools": [{"type": "function", "name": f["function"]["name"],
                              "description": f["function"].get("description", ""),
                              "inputSchema": f["function"].get("parameters") or {"type": "object", "properties": {}}}
                             for f in functions]}, deadline)
        if started.get("instructionSources"):
            raise live.Lost(f"Codex loaded instruction files: {started['instructionSources']}")
        self.thread = started["thread"]["id"]

    def _login(self, method, deadline, rid=None):
        """Give the session the user's access token: a login request, or the answer to Codex's refresh request."""
        try:
            access, account = tokens(self.owner.login_home, self.clock)
        except SetupError as e:
            if rid is not None:
                self._send({"id": rid, "error": {"code": -32000, "message": str(e)}}, deadline)
                return
            raise live.Lost(str(e)) from None
        found = {"accessToken": access, "chatgptAccountId": account, "chatgptPlanType": None}
        if rid is None:
            self._request(method, {"type": "chatgptAuthTokens", **found}, deadline)
        else:
            self._send({"id": rid, "result": found}, deadline)

    def _send(self, message, deadline):
        live.write_within(self.proc, json.dumps(message), deadline, self.clock, self.owner.timeout, self.ended_text)

    def _pull(self, deadline):
        """The next message from Codex, its bookkeeping done: a tool call request noted, a refresh request answered,
        any other request refused, a response to one of ours kept, the latest rate limits kept."""
        event = self.next_event(deadline)
        method, params = event.get("method"), event.get("params") or {}
        if method is not None and "id" in event:  # a request from Codex
            if method == "item/tool/call":
                self.requests[params.get("callId")] = event["id"]
            elif method == "account/chatgptAuthTokens/refresh":
                self._login(method, deadline, rid=event["id"])
            else:
                self._send({"id": event["id"], "error": {"code": -32601, "message": "not available here"}}, deadline)
        elif method is None and "id" in event:
            self.replies[event["id"]] = event
        elif method == "account/rateLimits/updated":
            self.limits = params.get("rateLimits")
        return event

    def _event(self, deadline):
        return self.backlog.popleft() if self.backlog else self._pull(deadline)

    def _request(self, method, params, deadline):
        """A request to Codex and its result; Lost on an error. Messages read meanwhile wait in the backlog."""
        self.n += 1
        rid = self.n
        self._send({"id": rid, "method": method, "params": params}, deadline)
        while rid not in self.replies:
            event = self._pull(deadline)
            if event.get("id") != rid or "method" in event:
                self.backlog.append(event)
        reply = self.replies.pop(rid)
        if "error" in reply:
            raise live.Lost(f"Codex refused {method}: {(reply['error'] or {}).get('message')}")
        return reply.get("result") or {}

    def send_user(self, text, deadline):
        params = {"threadId": self.thread, "summary": "detailed",
                  "input": [{"type": "text", "text": text, "text_elements": []}]}
        if self.owner.effort:
            params["effort"] = self.owner.effort
        self._request("turn/start", params, deadline)

    def send_answers(self, answers, deadline):
        """The market's answers by call id, each as the result of Codex's request for that call."""
        for call_id, text in answers.items():
            while call_id not in self.requests:
                self.backlog.append(self._pull(deadline))
            self._send({"id": self.requests.pop(call_id), "result": {
                "contentItems": [{"type": "inputText", "text": text}], "success": True}}, deadline)

    def response(self, deadline):
        """The next model response, from the raw items of the turn up to the response's end; (None, why) when the
        turn ends first."""
        calls, texts, thinking, items = [], [], [], 0
        while True:
            event = self._event(deadline)
            method, params = event.get("method"), event.get("params") or {}
            if method == "rawResponseItem/completed":
                item = params.get("item") or {}
                kind = item.get("type")
                if kind == "message" and item.get("role") == "assistant":
                    texts += [c.get("text", "") for c in item.get("content") or []
                              if isinstance(c, dict) and c.get("type") == "output_text"]
                elif kind == "reasoning":
                    thinking += [s.get("text", "") for s in item.get("summary") or [] if isinstance(s, dict)]
                elif kind == "function_call":
                    arguments = item.get("arguments")
                    calls.append({"id": item.get("call_id"), "name": item.get("name"), "served": item.get("name")
                                  in self.tools, "arguments": arguments if isinstance(arguments, str)
                                  else json.dumps(arguments)})
                elif kind in ("message", "function_call_output", "custom_tool_call_output"):
                    continue  # the user's input, a tool's output: not the model's
                else:  # any other output (another kind of call): not a function of the market's
                    calls.append({"id": item.get("call_id") or item.get("id"), "name": str(kind), "served": False,
                                  "arguments": json.dumps(item)})
                items += 1
            elif method == "rawResponse/completed":
                usage = params.get("usage") or {}
                if not items and not live.count(usage.get("outputTokens")):
                    continue  # a request with no model output (Codex warming its connection)
                return live.Response(
                    calls=calls, text="".join(texts), thinking=live.joined(*thinking),
                    tokens_in=live.count(usage.get("inputTokens")), tokens_out=live.count(usage.get("outputTokens")),
                    reasoning_tokens=live.count(usage.get("reasoningOutputTokens")),
                    finish="tool_calls" if calls else "stop", waits=bool(calls)), None
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("status") == "completed":
                    return None, "the turn completed"
                error = turn.get("error") or {}
                if error.get("codexErrorInfo") in _LIMITED:
                    self.until = self._reset()
                return None, error.get("message") or turn.get("status")

    def _reset(self):
        """When a reached subscription limit resets (seconds since the epoch), from the latest rate limits."""
        windows = [(self.limits or {}).get(k) or {} for k in ("primary", "secondary")]
        reached = [w.get("resetsAt") for w in windows
                   if isinstance(w.get("resetsAt"), (int, float)) and live.count(w.get("usedPercent")) >= 100]
        return max(reached) if reached else None

    def close(self):
        if self.proc is not None:
            live.stop_process(self.proc)
        if getattr(self, "dir", None):
            shutil.rmtree(self.dir, ignore_errors=True)


class _Backend(live.Backend):
    provider = "codex"
    harness = "Codex"
    session = _Session

    def __init__(self, spec, llm, tracker, cli, login_home, catalog_text, sleep, clock):
        super().__init__(spec, llm, tracker, sleep, clock)
        self.cli, self.login_home, self.catalog = cli, login_home, catalog_text
