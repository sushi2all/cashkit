# Pending Spec Updates

Generated: 2026-08-05 · Source: design session 2026-08-05 (ADR-0015 … ADR-0020)

Not applied. Each entry needs approval before editing the target file.

**Disposition 2026-08-05 (ADR-0021):** superseded before application. The agent implementation — and with it PRD §9 and the agent-related §11 acceptance criteria — is reclassified as app-layer material; the engine core neither implements nor specifies agent behaviour. These staged edits are retained as design record for the app-layer agent work (with ADR-0015/0019 and `km/notes/intent-schema-draft.md`). The one engine-side piece (ADR-0020's CK-I010…I015 diagnostics) was cancelled before implementation.

---

## From ADR-0015: The agent is a command interpreter, not a financial advisor

### 1. PRD §9.5 — "Required agent behaviour"

**Change type:** modification (removes a mandate)
**Description:** The block requiring the agent to run the non-native tax checklist, produce a coverage statement, and never present a forecast without one is deleted. Replaced by a pointer to the `validate()` diagnostics defined in ADR-0020. The "what the engine handles natively" and "what must be modelled manually" tables stay: they are reference material, still correct.

> Coverage of non-native tax mechanics is reported by the engine, not judged by the agent. `validate()` emits one info diagnostic per uncovered mechanic (CK-I010 … CK-I015); the coverage statement is a rendering of those diagnostics. The agent surfaces them verbatim and does not add commentary, ask the user about mechanics absent from the book, or advise on tax treatment.

### 2. PRD §9.6 — installation instructions, step 4

**Change type:** modification
**Description:** Replace "After `init`, immediately run the §9.5 tax checklist."

> 4. After `init`, run `validate()` and surface any diagnostics returned.

### 3. PRD §9.3 — mandatory behavioural rules

**Change type:** addition
**Description:** Add an 11th rule bounding scope. Existing rules 1–10 are all mechanical and survive unchanged.

> 11. **Do not advise.** Interpret the command, call the SDK, report the result. Never ask the user about facts not present in the book, never recommend a tax, financing or business treatment, never assess whether a forecast is complete. Completeness is reported by `validate()`.

### 4. PRD §10 — acceptance criteria, "Agent usability"

**Change type:** modification
**Description:** The fourth bullet binds "done" to advisory behaviour that no longer exists.

> - An agent given only SKILL.md maps 20 natural-language commands to the correct intent and slots, builds a 20-item book with VAT and a downside scenario, and surfaces `validate()` diagnostics verbatim.

### 5. PRD §7.1 — explicitly not built

**Change type:** addition (new table row)

> | Financial, tax or business advice | The agent drives the SDK; judgement stays with the user and their commercialista | Engine reports coverage via diagnostics (§10.1) |

---

## From ADR-0016: The engine and SDK never call a model

### 6. CLAUDE.md — Non-negotiables

**Change type:** addition

> - `cashkit/` has no LLM dependency. No package under `cashkit/` imports a model client, calls an inference endpoint, or embeds a prompt. Lint-enforced alongside the wall-clock ban.

### 7. PRD §8.2 — dependency set

**Change type:** clarification
**Description:** Add one line after the dependency list.

> No extra pulls an inference stack. Model runtimes are a host concern and live outside the package (ADR-0016).

---

## From ADR-0017: Local-first is an adoption requirement

### 8. PRD §7.3 — deferred, not rejected

**Change type:** modification
**Description:** The UI sentence describes local-first as a simplification. Extend it to record the two target configurations and that offline mobile is a separate initiative.

> Local execution is an adoption requirement, not a convenience (ADR-0017): configuration A (on-premises host, local open-weight model) needs no engine change; configuration B (offline mobile, on-device engine and model) is a post-v1 product line with its own gates.

### 9. PRD §8.1 — requirements

**Change type:** clarification
**Description:** "No system services. Everything is a file." remains true of CashKit and stops being true of the deployment once local inference is added. Say so rather than letting it read as an unqualified guarantee.

> No system services. Everything is a file. (A local model runtime, where used, is the exception and is not a CashKit dependency.)

---

## From ADR-0018: The revision store is an interface

### 10. PRD §6.6 — version control

**Change type:** clarification
**Description:** §6.6 currently specifies `at()` in terms of pygit2 object-store reads. State the seam.

> `commit()`, `history()` and `at()` are defined against a revision-store interface (write revision, list revisions, read state at revision, diff revisions). Git via pygit2 is the v1 implementation; no git type appears in the interface signatures (ADR-0018).

### 11. PROMPT-fable5-implementation.md — S5 gate

**Change type:** addition
**Description:** The session that builds the git store must build the interface first. Add to that session's gate criteria.

> The revision store lands as an interface with a git implementation behind it. Gate evidence: no `pygit2` import outside `stores/`, and the interface signatures carry no git-native types.

---

## From ADR-0019: The agent surface is an enumerated intent grammar

### 12. PRD §9 — new subsection

**Change type:** addition
**Description:** New §9.7 specifying the intent surface, plus the two SDK obligations it creates.

> **§9.7 Intent surface.** The agent emits one of an enumerated set of intents with typed slots under a fixed schema, not free-form SDK calls. Consequences for the SDK: every reportable question is answerable in a single call (no agent-side composition of `frame()` plus group-by), and `as_of` is a host-supplied slot, never model-generated. Read intents and mutation intents are separate sets so a host can gate the second.

### 13. PROMPT-fable5-implementation.md — prerequisite work

**Change type:** addition
**Description:** New work item, ahead of remaining engine phases.

> Draft the intent schema (15–25 intents) and score it against a small (~3B) and a mid-size (~30B) model before freezing the SDK query surface. Output: the list of single-call reporting verbs the SDK owes.

---

## From ADR-0020: Non-native tax coverage is a deterministic diagnostic

### 14. PRD §10.1 — diagnostic catalogue

**Change type:** addition
**Description:** CK-I001 already covers the generic case and stays. Add one code per mechanic so the coverage statement can be rendered from diagnostics alone.

> | CK-I010 | No `cat:tax` item covering IRES/IRAP (advances typically June and November) |
> | CK-I011 | No `cat:tax` item covering INPS/INAIL contributions |
> | CK-I012 | No stock item covering TFR accrual |
> | CK-I013 | No event covering acconto IVA (December) |
> | CK-I014 | No item covering tax credits or incentives |
> | CK-I015 | No events covering instalment plans, ravvedimento or penalties |

### 15. PRD §9.5 — closing line

**Change type:** modification
**Description:** The existing final sentence describes CK-I001 as supporting the agent checklist. Restate it as the mechanism.

> `validate()` implements this: CK-I001 for the generic case, CK-I010 … CK-I015 per mechanic. Severity is info throughout, because a book that does not model a real legal entity legitimately has none of them.
