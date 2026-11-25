#!/usr/bin/env python3
"""Ask the assistant of a started Genesis one question through its front door, as the Ask page does.

    python3 tools/ask_check.py [http://127.0.0.1:8080] [--model qwen3.5-abliterated:4b]

Run it after `genesis start` on a vault that holds a model. It exits non zero when the front door lists no
model, when the model named is not among them, or when the answer comes back empty. It needs nothing but
Python, so it runs wherever Genesis does.
"""

import json
import sys
import time
import urllib.request

args = sys.argv[1:]
want = None
if "--model" in args:
    at = args.index("--model")
    want = args[at + 1]
    del args[at:at + 2]
base = (args[0] if args else "http://127.0.0.1:8080").rstrip("/")

found = []
for _ in range(60):  # the model server can take a moment after the front door is up
    with urllib.request.urlopen(base + "/api/ai/models", timeout=30) as r:
        listed = json.loads(r.read())
    found = [m["name"] for m in listed.get("models", [])]
    if found:
        break
    time.sleep(2)
print("models on the drive:", ", ".join(found) or "none")
if not found:
    sys.exit("FAIL: the front door lists no model")
model = next((m for m in found if want and want in m), None) if want else found[0]
if not model:
    sys.exit("FAIL: no model named %s" % want)

question = "In one short sentence: what two elements is water made of?"
ask = json.dumps({"model": model, "messages": [{"role": "user", "content": question}]}).encode()
req = urllib.request.Request(base + "/api/ai/chat", data=ask, headers={"Content-Type": "application/json"})
began, words, done = time.time(), [], {}
with urllib.request.urlopen(req, timeout=900) as r:
    for line in r:
        if not line.strip():
            continue
        part = json.loads(line)
        if part.get("error"):
            sys.exit("FAIL: " + part["error"])
        words.append((part.get("message") or {}).get("content") or "")
        if part.get("done"):
            done = part
answer = "".join(words).strip()
print("asked %s: %s" % (model, question))
print("answer in %.0f s: %s" % (time.time() - began, answer))
if done.get("eval_count") and done.get("eval_duration"):
    print("speed: %.1f tokens a second" % (done["eval_count"] / (done["eval_duration"] / 1e9)))
if not answer:
    sys.exit("FAIL: the answer was empty")
if "hydrogen" not in answer.lower() or "oxygen" not in answer.lower():
    print("::warning::the answer does not name hydrogen and oxygen")
print("OK")
