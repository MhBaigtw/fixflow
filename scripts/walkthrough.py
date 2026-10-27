"""Scripted HTTP walkthrough of the FixFlow workflow against a running server.

Usage:
    .venv\\Scripts\\python.exe manage.py seed_demo
    .venv\\Scripts\\python.exe scripts\\walkthrough.py [BASE_URL]   # default http://127.0.0.1:8000

Needs DEMO_PASSWORD in the environment or in .env. Uses only the standard library.
Tokens are never printed. Exits non-zero if any response has an unexpected status.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
REDACT = {"access", "refresh"}
failures = []


def demo_password():
    if os.environ.get("DEMO_PASSWORD"):
        return os.environ["DEMO_PASSWORD"]
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DEMO_PASSWORD="):
                return line.split("=", 1)[1].strip()
    sys.exit("DEMO_PASSWORD is not set (env or .env).")


def call(method, path, token=None, body=None, expect=200, label="", show=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    payload = json.loads(raw) if raw else None

    shown = (
        {k: "<redacted>" if k in REDACT else v for k, v in payload.items()}
        if isinstance(payload, dict)
        else payload
    )
    mark = "ok " if status == expect else "BAD"
    print(f"[{mark}] {label}\n      {method} {path} -> {status}")
    if show:
        print("      " + json.dumps(shown, default=str))
    if status != expect:
        failures.append(f"{label}: expected {expect}, got {status}")
    return payload


def login(email, password):
    body = {"email": email, "password": "<redacted>"}
    print(f"      (login body: {json.dumps(body)})")
    tokens = call(
        "POST",
        "/api/auth/token/",
        body={"email": email, "password": password},
        label=f"login {email}",
    )
    return tokens["access"]


def summary(req):
    keys = ["id", "status", "assigned_to", "resolved_at", "closed_at", "sla_due_at"]
    return {k: req.get(k) for k in keys}


def main():
    password = demo_password()
    print(f"FixFlow walkthrough against {BASE_URL}\n")

    call("GET", "/api/health/", label="health check")

    alice = login("alice@fixflow.example", password)  # tenant, Maple Court 1A
    manager = login("manager@fixflow.example", password)
    plumber = login("plumber@fixflow.example", password)  # contractor
    carol = login("carol@fixflow.example", password)  # tenant, Maple Court 1B (other unit)

    props = call("GET", "/api/properties/", manager, label="manager lists properties", show=False)
    maple = next(p for p in props["results"] if p["name"] == "Maple Court")
    units = call(
        "GET", f"/api/properties/{maple['id']}/units/", manager, label="list units", show=False
    )
    unit_1a = next(u for u in units["results"] if u["label"] == "1A")
    plumber_id = call("GET", "/api/me/", plumber, label="plumber /me/")["id"]

    print("\n--- Tenant creates a request")
    req = call(
        "POST",
        "/api/requests/",
        alice,
        {
            "unit": unit_1a["id"],
            "title": "Shower has no hot water",
            "description": "Since this morning, cold water only.",
            "category": "plumbing",
            "priority": "urgent",
        },
        expect=201,
        label="alice creates request",
    )
    rid = req["id"]

    def transition(token, action, label, expect=200, **extra):
        resp = call(
            "POST",
            f"/api/requests/{rid}/transition/",
            token,
            {"action": action, **extra},
            expect=expect,
            label=label,
            show=False,
        )
        print("      " + json.dumps(summary(resp) if expect == 200 else resp, default=str))
        return resp

    print("\n--- Manager triages and assigns; contractor starts and resolves")
    transition(manager, "triage", "manager triages", note="Checking boiler first")
    transition(manager, "assign", "manager assigns plumber", contractor_id=plumber_id)
    transition(plumber, "start", "plumber starts")
    transition(plumber, "resolve", "plumber resolves", note="Reset boiler pressure")

    print("\n--- Tenant reopens (within the 14-day window)")
    transition(alice, "reopen", "alice reopens", note="Cold again by evening")

    print("\n--- Second pass through to closed")
    transition(manager, "assign", "manager re-assigns plumber", contractor_id=plumber_id)
    transition(plumber, "start", "plumber starts again")
    transition(plumber, "resolve", "plumber resolves", note="Replaced faulty thermostat")
    transition(alice, "close", "alice closes")

    print("\n--- Isolation and invalid moves")
    call("GET", f"/api/requests/{rid}/", carol, expect=404, label="carol (other unit) GET detail")
    call(
        "POST",
        f"/api/requests/{rid}/transition/",
        carol,
        {"action": "reopen"},
        expect=404,
        label="carol (other unit) tries to reopen",
    )
    transition(manager, "triage", "manager tries to triage a closed request", expect=409)

    print("\n--- Audit log")
    events = call(
        "GET", f"/api/requests/{rid}/events/", alice, label="alice reads audit log", show=False
    )
    for e in events["results"]:
        print(
            f"      {e['created_at']}  actor={e['actor']:<3} "
            f"{str(e['from_status']):<12} -> {e['to_status']:<12} {e['note']}"
        )

    print()
    if failures:
        print("FAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("All steps returned the expected status codes.")


if __name__ == "__main__":
    main()
