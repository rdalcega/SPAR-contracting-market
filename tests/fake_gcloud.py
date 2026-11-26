"""A scripted stand-in for `gcloud config config-helper`, for tests/test_vertex.py; no network, no login.

Usage: fake_gcloud.py STATE [gcloud's arguments]. STATE is a JSON file {"next": i, "responses": [...], "runs": [...]}:
each run takes the next response (the last one repeats) and adds its arguments to "runs". A response is one of:
  {"token": text, "expiry": epoch seconds}   prints what config-helper prints for that token; with "sticky": true it
      is gcloud's cached token: runs keep getting it until one passes --force-auth-refresh, which takes the next
  {"fail": text}                             prints text to stderr and exits with status 1
  {"raw": text}                              prints text as it is
"""

import datetime
import json
import sys

STATE, ARGS = sys.argv[1], sys.argv[2:]

with open(STATE) as f:
    state = json.load(f)
responses = state["responses"]
i = min(state["next"], len(responses) - 1)
if responses[i].get("sticky") and "--force-auth-refresh" in ARGS:
    i = min(i + 1, len(responses) - 1)
response = responses[i]
state["next"] = i if response.get("sticky") else i + 1
state["runs"].append(ARGS)
with open(STATE, "w") as f:
    json.dump(state, f)

if "fail" in response:
    sys.stderr.write("WARNING: something first\n" + response["fail"] + "\n")
    sys.exit(1)
if "raw" in response:
    sys.stdout.write(response["raw"])
    sys.exit(0)
expiry = datetime.datetime.fromtimestamp(response["expiry"], datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps({"configuration": {}, "credential": {"access_token": response["token"], "token_expiry": expiry},
                  "sentinels": {}}))
