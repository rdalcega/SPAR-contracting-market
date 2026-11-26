"""Provider vertex: Google Cloud's Vertex AI through its OpenAI-compatible endpoint, with the user's gcloud login
(`gcloud auth login`).

models.md 3 lets another provider be added behind the same interface. Vertex speaks the chat-completions protocol, so
this provider is openai_compatible (section 4 with that row's differences: no usage field, no X-Title) except for its
key. Vertex takes a Google access token that lasts an hour, so the backend asks gcloud for one and asks again before
it runs out (SPEC_QUESTIONS Q119):
- the spec gives `base_url`, which names the project and region (config refuses a vertex spec without one), for
  example https://aiplatform.googleapis.com/v1/projects/PROJECT/locations/global/endpoints/openapi; it sets no
  `api_key_env`;
- the token comes from `gcloud config config-helper`, which returns the login's access token and its expiry; it is
  fetched again when less than MARGIN is left, and once more, with gcloud made to refresh it, when a request is
  refused with HTTP 401;
- Vertex reports no dollar cost, so a reply costs its tokens at the spec's prices (4.9); output tokens that `usage`
  counts in total_tokens but not in completion_tokens (Gemini's thinking, which Google bills as output) are added to
  the output tokens.
"""

import datetime
import json
import shutil
import subprocess
import threading

from .models import SetupError, _Backend, _Failed

CLI = "gcloud"
MARGIN = 600  # seconds: a token with less than this left is fetched again
_ASK = ("config", "config-helper", "--format=json")
_FRESH = f"--min-expiry={2 * MARGIN}s"  # gcloud refreshes its cached token when less than this is left
_FORCED = "--force-auth-refresh"  # gcloud refreshes it whatever is left (after a 401)
_WAIT = 120  # seconds: the longest a gcloud run may take


def backend(spec, llm, tracker, *, cli=None, sleep, clock):
    """models.md 3, "Setting up": the backend of a vertex spec; SetupError when gcloud is missing or gives no token."""
    cli = cli or CLI
    if shutil.which(cli) is None:
        raise SetupError(f"provider vertex needs Google Cloud's `{cli}` command on PATH")
    return _VertexBackend(spec, llm, tracker, cli, sleep, clock)


def token(cli, force=False):
    """The gcloud login's access token and its expiry (epoch seconds), else _Failed with gcloud's error; `force`
    makes gcloud refresh it even when its cached one has not run out."""
    try:
        done = subprocess.run([cli, *_ASK, _FORCED if force else _FRESH], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=_WAIT)  # no stdin: a sign-in prompt fails at once
    except (OSError, subprocess.SubprocessError) as e:
        raise _Failed(f"gcloud could not be run: {type(e).__name__}: {e}") from None
    if done.returncode:
        lines = done.stderr.strip().splitlines() or [f"exit status {done.returncode}"]
        raise _Failed(f"gcloud gave no token: {lines[-1][:300]}")
    try:
        found = json.loads(done.stdout)["credential"]
        access = found["access_token"]
        expiry = datetime.datetime.fromisoformat(found["token_expiry"])
        expiry = (expiry if expiry.tzinfo else expiry.replace(tzinfo=datetime.UTC)).timestamp()
        if not (isinstance(access, str) and access and access.isascii() and access.isprintable()
                and " " not in access):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise _Failed("gcloud gave no access token with an expiry") from None
    return access, expiry


class _VertexBackend(_Backend):
    """The OpenAI-compatible backend, its Authorization header kept on a token that has not run out."""

    def __init__(self, spec, llm, tracker, cli, sleep, clock):
        super().__init__(spec, llm, tracker, spec.get("base_url"), None, sleep, clock)
        self.cli, self.lock, self.expiry = cli, threading.Lock(), None
        try:
            self._sign()
        except _Failed as e:
            raise SetupError(f"provider vertex needs a gcloud login: run `gcloud auth login` ({e})") from None

    def _attempt(self, data):
        headers = self._sign()
        status, raw = super()._attempt(data, headers)
        if status == 401:
            status, raw = super()._attempt(data, self._sign(refused=headers))
        return status, raw

    def _sign(self, refused=None):
        """The headers, on a token with more than MARGIN left; after a 401 with the `refused` headers, on a new token
        (unless another call has already replaced it)."""
        with self.lock:
            force = refused is self.headers
            if force or self.expiry is None or self.expiry - self.clock() < MARGIN:
                access, self.expiry = token(self.cli, force)
                self.headers = {**self.headers, "Authorization": f"Bearer {access}"}
            return self.headers

    def _read(self, body, sent):
        """4.7 to 4.9, with output tokens that usage leaves out of completion_tokens but counts in total_tokens
        (Gemini's thinking, billed as output) added to them."""
        usage = body.get("usage")
        counts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")] if isinstance(
            usage, dict) else []
        if counts and all(type(n) is int for n in counts) and counts[2] > counts[0] + counts[1]:
            body = {**body, "usage": {**usage, "completion_tokens": counts[2] - counts[0]}}
        return super()._read(body, sent)
