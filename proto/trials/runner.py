"""Trial helper: drive the proto server and check cells.

Usage from a trial script:
    from runner import chat, state, reset, cell, closing, expect
"""
from __future__ import annotations

import json
import re
import urllib.request

BASE = "http://localhost:8765"
_history: list[dict] = []
FAILS: list[str] = []


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.load(resp)


def reset() -> None:
    _history.clear()
    _post("/api/reset", {})


def chat(message: str, model: str = "lite") -> dict:
    out = _post("/api/chat", {"message": message, "model": model, "history": _history})
    _history.extend([{"role": "user", "content": message},
                     {"role": "assistant", "content": out.get("reply", "")}])
    rounds = out.get("rounds", [])
    nops = sum(len(r.get("ops", [])) for r in rounds)
    errs = [d for r in rounds for rep in r.get("reports", [])
            for d in rep["diagnostics"] if d["severity"] == "error"]
    errs += [d for r in rounds for d in r.get("run_errors", [])]
    print(f"  chat: {len(rounds)} round(s), {nops} ops, {len(errs)} error diag(s)")
    for r in rounds:
        if r.get("error"):
            print(f"    ! round error: {r['error'][:200]}")
            print(f"      raw: {(r.get('raw') or '')[:300]}")
    for d in errs:
        print(f"    ! {d['code']} {d['message'][:140]}")
    return out


def ops(op_list: list[dict]) -> dict:
    out = _post("/api/ops", {"ops": op_list})
    bad = [d for r in out["reports"] for d in r["diagnostics"] if d["severity"] == "error"]
    for d in bad:
        print(f"    ! ops: {d['code']} {d['message'][:120]}")
    return out


def export(query: str) -> bytes:
    with urllib.request.urlopen(BASE + "/api/export?" + query, timeout=60) as resp:
        return resp.read()


def upload(data: bytes, filename: str, model: str = "lite") -> dict:
    boundary = "----ckproto"
    body = b""
    for name, value in (("model", model),):
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                 f"{value}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
             ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/upload", data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        out = json.load(resp)
    rounds = out.get("rounds", [])
    nops = sum(len(r.get("ops", [])) for r in rounds)
    errs = [d for r in rounds for rep in r.get("reports", [])
            for d in rep["diagnostics"] if d["severity"] == "error"]
    print(f"  upload: {len(rounds)} round(s), {nops} ops, {len(errs)} error diag(s)")
    for r in rounds:
        if r.get("error"):
            print(f"    ! round error: {r['error'][:200]}")
    for d in errs:
        print(f"    ! {d['code']} {d['message'][:140]}")
    return out


def state(scenario: str = "base") -> dict:
    with urllib.request.urlopen(BASE + "/api/state?scenario=" + scenario, timeout=60) as resp:
        return json.load(resp)


def cell(s: dict, item_id: str, month: str, measure: str | None = None) -> float | None:
    for it in s["items"]:
        if it["id"] == item_id:
            key = measure or ("value" if it["kind"] == "stock" else "cash")
            try:
                return float(it[key][s["months"].index(month)])
            except (ValueError, IndexError):
                return None
    return None


def closing(s: dict, month: str) -> float | None:
    try:
        return float(s["closing"][s["months"].index(month)])
    except (ValueError, IndexError):
        return None


def find(s: dict, *words: str) -> str:
    """First item whose id+name contains every word at a word boundary."""
    for it in s["items"]:
        t = (it["id"] + " " + it["name"]).lower()
        if all(re.search(r"\b" + w, t) for w in words):
            return it["id"]
    return "?"


def expect(label: str, got: float | None, want: float, tol: float = 0.005) -> bool:
    ok = got is not None and abs(got - want) <= tol
    print(f"  {'PASS' if ok else 'FAIL'} {label}: got {got}, want {want}")
    if not ok:
        FAILS.append(label)
    return ok


def finish(name: str) -> None:
    print(f"== {name}: {'ALL PASS' if not FAILS else f'{len(FAILS)} FAIL: {FAILS}'}")
