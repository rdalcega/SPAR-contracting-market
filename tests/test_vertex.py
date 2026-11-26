"""Tests of provider vertex (market/vertex.py) against a scripted fake `gcloud` (tests/fake_gcloud.py) and the fake
chat-completions server of tests/test_models.py: free, offline."""

import json
import os
import sys

import pytest
from test_models import LLM, Fake, ok, resolved

from market import models, vertex
from market.config import ConfigError, resolve

FAKE = os.path.join(os.path.dirname(__file__), "fake_gcloud.py")
NOW = 2_000_000_000
SPEC = {"provider": "vertex", "model": "google/gemini-test", "price_in_per_m": 1.0, "price_out_per_m": 2.0}


class Gcloud:
    """A fake gcloud on disk: `responses` as fake_gcloud.py reads them; `runs` lists each run's arguments."""

    def __init__(self, tmp_path, responses):
        self.state = tmp_path / "gcloud.json"
        self.state.write_text(json.dumps({"next": 0, "responses": responses, "runs": []}))
        self.cli = tmp_path / "gcloud"
        self.cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "{self.state}" "$@"\n')
        self.cli.chmod(0o755)

    @property
    def runs(self):
        return json.loads(self.state.read_text())["runs"]


class Clock:
    def __init__(self):
        self.now, self.waits = float(NOW), []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


@pytest.fixture
def server():
    fake = Fake()
    yield fake
    fake.httpd.shutdown()
    fake.httpd.server_close()


def make(server, gcloud, clock=None, **llm):
    clock = clock or Clock()
    spec = resolved({**SPEC, "base_url": server.url})
    return vertex.backend(spec, {**LLM, **llm}, models.CostTracker(None), cli=str(gcloud.cli), sleep=clock.sleep,
                          clock=clock), clock


def auth(server):
    return [r.headers["Authorization"] for r in server.requests]


def test_config_needs_base_url_and_no_api_key_env():
    base = "https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi"
    with pytest.raises(ConfigError, match=r"models\.buyers\.base_url.*vertex"):
        resolve({"models": {"buyers": SPEC}})
    with pytest.raises(ConfigError, match=r"models\.buyers\.api_key_env.*gcloud"):
        resolve({"models": {"buyers": {**SPEC, "base_url": base, "api_key_env": "KEY"}}})
    out = resolve({"models": {"buyers": {**SPEC, "base_url": base, "temperature": 0.3}}})["models"]["buyers"]
    assert out["base_url"] == base and out["temperature"] == 0.3  # not a harness: temperature and base_url kept


def test_the_call_is_openai_compatible_with_the_gcloud_token(server, tmp_path):
    gcloud = Gcloud(tmp_path, [{"token": "tok-1", "expiry": NOW + 3600}])
    server.script = [ok("hello", usage={"prompt_tokens": 1000, "completion_tokens": 500})]
    b, _ = make(server, gcloud)
    r = b.call([{"role": "user", "content": "hi"}])
    assert r.text == "hello" and r.cost_source == "computed" and r.cost == pytest.approx(0.002)
    sent = server.requests[0]
    assert sent.path == "/v1/chat/completions" and sent.headers["Authorization"] == "Bearer tok-1"
    assert sent.headers["X-Title"] is None and "usage" not in json.loads(sent.raw)
    assert json.loads(sent.raw)["model"] == "google/gemini-test"
    assert gcloud.runs == [[*vertex._ASK, vertex._FRESH]]


def test_setup_fails_without_gcloud_or_a_login(server, tmp_path):
    with pytest.raises(models.SetupError, match="`gcloud-that-is-not-there` command on PATH"):
        vertex.backend(resolved({**SPEC, "base_url": server.url}), LLM, models.CostTracker(None),
                       cli="gcloud-that-is-not-there", sleep=None, clock=None)
    for response, why in (({"fail": "ERROR: no active account"}, "no active account"),
                          ({"raw": "not json"}, "no access token"),
                          ({"raw": json.dumps({"credential": {"access_token": "t"}})}, "no access token")):
        with pytest.raises(models.SetupError, match=f"gcloud auth login.*{why}"):
            make(server, Gcloud(tmp_path, [response]))


def test_models_backend_sends_vertex_specs_here(server, tmp_path, monkeypatch):
    gcloud = Gcloud(tmp_path, [{"token": "tok-1", "expiry": NOW + 3600}])
    monkeypatch.setattr(vertex, "CLI", str(gcloud.cli))
    b = models.backend(resolved({**SPEC, "base_url": server.url}), LLM, models.CostTracker(None), env={})
    assert isinstance(b, vertex._VertexBackend)


def test_the_token_is_fetched_again_before_it_runs_out(server, tmp_path):
    gcloud = Gcloud(tmp_path, [{"token": "tok-1", "expiry": NOW + 3600}, {"token": "tok-2", "expiry": NOW + 7200}])
    server.script = [ok()]
    b, clock = make(server, gcloud)
    b.call([{"role": "user", "content": "a"}])
    clock.now += 3600 - vertex.MARGIN - 1  # still more than MARGIN left
    b.call([{"role": "user", "content": "b"}])
    clock.now += 2  # now less
    b.call([{"role": "user", "content": "c"}])
    assert auth(server) == ["Bearer tok-1", "Bearer tok-1", "Bearer tok-2"] and len(gcloud.runs) == 2


def test_a_401_makes_gcloud_refresh_its_cached_token_once(server, tmp_path):
    gcloud = Gcloud(tmp_path, [{"token": "tok-1", "expiry": NOW + 3600, "sticky": True},
                               {"token": "tok-2", "expiry": NOW + 3600}])
    server.script = [(401, {"error": "revoked"}), ok("after")]
    b, clock = make(server, gcloud)
    assert b.call([{"role": "user", "content": "a"}]).text == "after"
    assert auth(server) == ["Bearer tok-1", "Bearer tok-2"] and clock.waits == []
    assert gcloud.runs[1] == [*vertex._ASK, vertex._FORCED]

    server.requests.clear()
    server.script = [(401, {"error": "still refused"})]
    with pytest.raises(models.CallFailed, match="HTTP 401"):
        b.call([{"role": "user", "content": "b"}])
    assert len(server.requests) == 2  # one new token, one more try; a 401 is not retried after that


def test_a_failed_refresh_is_a_failed_attempt_that_is_retried(server, tmp_path):
    gcloud = Gcloud(tmp_path, [{"token": "tok-1", "expiry": NOW + 60}, {"fail": "ERROR: network"},
                               {"token": "tok-2", "expiry": NOW + 3600}])
    server.script = [ok("fine")]
    b, clock = make(server, gcloud)
    assert b.call([{"role": "user", "content": "a"}]).text == "fine"
    assert len(clock.waits) == 1 and auth(server) == ["Bearer tok-2"]


def test_output_tokens_left_out_of_completion_tokens_are_counted(server, tmp_path):
    gcloud = Gcloud(tmp_path, [{"token": "tok-1", "expiry": NOW + 3600}])
    server.script = [ok("hi", usage={"prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1500}),
                     ok("hi", usage={"prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1010})]
    b, _ = make(server, gcloud)
    thinking = b.call([{"role": "user", "content": "a"}])
    assert thinking.output_tokens == 500 and thinking.cost == pytest.approx(0.002)
    assert b.call([{"role": "user", "content": "b"}]).output_tokens == 10


def test_a_401_after_another_call_replaced_the_token_does_not_refresh_again(server, tmp_path):
    gcloud = Gcloud(tmp_path, [{"token": "tok-1", "expiry": NOW + 3600}, {"token": "tok-2", "expiry": NOW + 3600}])
    b, _ = make(server, gcloud)
    stale = b._sign()
    assert b._sign(refused=stale)["Authorization"] == "Bearer tok-2"  # the first 401 gets a new token
    assert b._sign(refused=stale)["Authorization"] == "Bearer tok-2" and len(gcloud.runs) == 2  # a later one not


def test_an_expiry_without_a_timezone_is_utc(tmp_path):
    gcloud = Gcloud(tmp_path, [{"raw": json.dumps({"credential": {"access_token": "t",
                                                                 "token_expiry": "2033-05-18T03:33:20"}})}])
    assert vertex.token(str(gcloud.cli)) == ("t", NOW)
