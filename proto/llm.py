"""OpenRouter client + prompts for the CashKit proto webapp.

ponytail: stdlib urllib, blocking, no streaming — one JSON call per turn.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ENV_PATH = Path("/Users/luca/dev/.env")
USAGE_LOG = Path(__file__).parent / "usage_log.jsonl"

MODELS = {
    "lite": "google/gemini-2.5-flash-lite",
    "flash": "google/gemini-3.7-flash",
    "sonnet": "anthropic/claude-sonnet-5",
    "opus": "anthropic/claude-opus-5",
}
DEFAULT = "lite"


def api_key() -> str:
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("OPENROUTER_API_KEY not found in " + str(ENV_PATH))


def key_status() -> dict:
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/auth/key",
        headers={"Authorization": "Bearer " + api_key()},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["data"]


def complete(messages: list[dict], model: str, max_tokens: int = 16000,
             temperature: float = 0.0) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    body = None
    for attempt in range(3):
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + api_key(),
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                body = json.load(resp)
            if "choices" in body and body["choices"]:
                break
            # provider-level error inside a 200
            if attempt == 2:
                raise RuntimeError("LLM error: " + json.dumps(body)[:500])
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    usage = body.get("usage", {})
    with USAGE_LOG.open("a") as fh:
        fh.write(json.dumps({
            "model": model,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cost": usage.get("cost"),
        }) + "\n")
    return body["choices"][0]["message"]["content"] or ""


def extract_json(text: str) -> dict:
    """Parse the model's JSON object; tolerate fences and surrounding prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in output")
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except json.JSONDecodeError:
        obj, _end = json.JSONDecoder().raw_decode(_repair(text[start:]))
        return obj


def _repair(text: str) -> str:
    """Fix the dominant small-model failure: unbalanced brackets.

    Walk the string outside string literals; when a closer contradicts the
    stack, insert the expected closers; append whatever is left open at the end.
    """
    out: list[str] = []
    stack: list[str] = []
    in_str = False
    escape = False
    for ch in text:
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            while stack and stack[-1] != ch:
                out.append(stack.pop())  # insert the closer the model forgot
            if stack:
                stack.pop()
            else:
                continue  # stray closer: drop it
        out.append(ch)
    if in_str:
        out.append('"')
    while stack:
        out.append(stack.pop())
    return "".join(out)


OPS_GUIDE = """You translate the user's budgeting instructions into CashKit operations.
CashKit is a deterministic cash-flow engine: a book has a monthly (or daily) horizon,
an opening balance, items that generate signed money amounts, and derived items
computed by formula. Storage is signed: outflows are NEGATIVE numbers.

Respond with ONE JSON object and nothing else:
{"reply": "<one short sentence for the user>", "ops": [ ... ]}

Operations (apply in order):

{"op":"create_book","start":"2026-01-01","end":"2027-01-01","opening_balance":"150.00",
 "grain":"month","params":{"overdraft_rate":"0.0795"}}
  - end is EXCLUSIVE: Jan..Dec 2026 means end 2027-01-01. Replaces any existing book.
  - only create a book when the user starts a new budget; never to edit one.

{"op":"add_item","item":{"id":"rent","name":"Rent share","kind":"flow","direction":"out",
 "tags":{"cat":"housing","cf":"cash"},
 "segments":[{"start":"2026-01-01","end":null,
   "recurrence":{"every":1,"unit":"month"},"amount":{"constant":"-455.00"}}],
 "settlement":"immediate"}}
  - direction "out" REQUIRES a negative amount; "in" a positive one.
  - segment end is exclusive; null/omitted = open-ended. Several segments = phases.
  - a line whose value differs month by month: ONE segment whose amount is
    {"schedule":[{"date":"2026-01-01","amount":"515.00"},{"date":"2026-02-01","amount":"490.00"},...]}
    Each schedule entry fires exactly ONCE on its date (it is not a step function).
    Months with zero: simply omit the entry.
  - settlement: "immediate" | {"net":30} | {"due":[{"share":"0.5","offset":"0d"},...]}.
    offsets are strings with a unit: "45d" = 45 days, "2m" = 2 months. shares sum to 1.
  - re-authoring: add_item with an existing id REPLACES that item entirely.
  - tag every real cash line with "cf":"cash" so formulas can aggregate them.

{"op":"add_derived","id":"revenue","formula":"p.day_rate * p.days_per_month",
 "tags":{"cf":"cash"},"name":"Consulting revenue"}
  - a derived item's value is computed per period by the formula and it SETTLES INTO
    CASH like any flow. Use it for computed money lines (param-driven revenue, fees,
    interest, taxes). A segment amount is ALWAYS a literal constant or schedule —
    there is no such thing as {"amount":{"formula":...}}; computed lines are derived
    items, never segments.
  - formulas must produce NEGATIVE values for outflows.

{"op":"add_derived","id":"closing_cash","formula":"...","kind":"stock","name":"Closing cash"}
  - kind "stock" = pure indicator: computed but NEVER cash. Use for closing balance,
    running totals, headroom, counters. Omitting kind:"stock" on an indicator
    double-counts money — a serious error.

Formula language (Python-like expressions; there is NO if, NO and/or, NO % operator):
  it("id")                value of another item in the same period
  agg(tag="cf:cash")      sum of every item whose tags match the selector
  prev("id", init=150)    previous period's value of an item (the only way to look back;
                          self-reference allowed: running balances use prev on themselves)
  cum("id")               running total of an item from the horizon start
  where(cond, a, b)       elementwise if/else; cond may use < <= > >= == !=
  p.key                   a named param (set via set_param / create_book params)
  min(a,b) max(a,b) clip(x,lo,hi) abs_(x) round_(x, ndigits=2)
  round_'s ndigits is KEYWORD-ONLY, 0..4: round_(x) rounds to whole units;
  round_(x, ndigits=2) to cents. round_(x, 2) is REJECTED.
  Operators: + - * / and unary minus. Numbers are exact decimals.
  Formulas are parsed and DAG-checked when the op is applied: an item referenced by a
  formula must already exist, so ORDER your ops dependencies-first. The only legal
  forward reference is prev("x") inside x's own formula (or a cycle broken by prev).

{"op":"set_param","key":"overdraft_rate","value":"0.0795"}

{"op":"add_event","event":{"id":"laptop-2026-09","date":"2026-09-15","amount":"-749.00",
 "status":"forecast","item":"one_offs"}}
  - one-off dated amounts can be events attached to an existing item.

ONE-TIME amounts (a single invoice, a purchase, an annual premium, a one-month bonus):
either a schedule amount with exactly one entry, or a segment whose end is one period
after its start. NEVER an open-ended or unbounded segment — that repeats every month.
Anything the user says happens "once", "in June", "annual", "one-off" must appear in
exactly one period of the year.

{"op":"fork_scenario","parent":"base","id":"downside","note":"..."}
{"op":"set_scenario_param","scenario":"downside","key":"headcount","value":"15"}
{"op":"retag","selector":"cat:revenue","tags":{"flag":"risky"}}

Rules:
- Money values are strings with a dot decimal separator, max 4 decimals.
- Dates are ISO "YYYY-MM-DD". Monthly amounts land on the 1st of the month.
- Prefer the simplest translation: a cost that starts in June is a segment starting
  2026-06-01, not a conditional formula.
- The engine tracks the cash balance itself from the opening balance and all cash
  items. Only author a closing-balance stock item when a FORMULA needs to read the
  previous month's balance (e.g. conditional overdraft fees).
- Mutually recursive pair (e.g. a fee computed from the PREVIOUS month's closing
  balance, where the balance also includes the fee): no single order passes the DAG
  check. Author it in three ops: (1) add the fee with formula "0", (2) add the
  closing-balance stock (prev on itself + agg over the cash tag), (3) re-author the
  fee with its real formula — add_derived with the same id replaces it.
- Never weaken the user's semantics to silence a diagnostic: if the instruction says
  "previous month's balance", the formula must keep prev(...). Fix the structure, not
  the meaning.
- If the instruction is a question about the current book rather than a change,
  answer in "reply" with "ops": []. The state carries computed results per scenario
  (closing balance per month, minimum cash, per-item year totals). Answer numeric
  questions FROM those numbers — quote them, subtract them, never recompute items
  from their segments and never invent a figure that is not derivable from results.
- After your ops are applied the engine runs and any diagnostics come back to you;
  fix them when asked.
"""


def chat_system(state_json: str) -> list[dict]:
    return [{"role": "system", "content": OPS_GUIDE + "\nCurrent book state:\n" + state_json}]


UPLOAD_GUIDE = """The user uploaded a spreadsheet of an existing budget. Translate it into
CashKit ops that recreate the budget: one create_book (infer the horizon from the month
columns, grain "month", opening balance if present), then add_item / add_derived ops for
every real money line. Skip subtotal/total/balance rows — the engine recomputes those —
unless a formula line needs them as a stock indicator.
Line values that vary month to month become schedule amounts (one entry per non-zero month).
A month showing 0 or blank in a line means NO amount that month: no schedule entry, or a
segment window that excludes it. Check each line month by month — do not assume a pattern.
A row labeled "Opening balance" (or the sheet's starting-cash assumption) is the
create_book opening_balance, never an item. Rows named closing/total/net are computed
results — skip them.
Income lines: direction "in", positive. Expense lines: direction "out", negative amounts
(the sheet may show them positive — negate them).
Respond with the same single JSON object: {"reply": "...", "ops": [...]}.
"""


def upload_system(state_json: str) -> list[dict]:
    return [{"role": "system", "content": OPS_GUIDE + UPLOAD_GUIDE + "\nCurrent book state:\n" + state_json}]
