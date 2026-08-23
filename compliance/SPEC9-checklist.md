# SPEC §9 compliance checklist — item by item, with evidence

**ADR-0026 rule 4 and SPEC §9: no external user before this checklist is
green.** This page is the gate. It is written so that each line names what was
actually run, and where an item is **not** green it says so rather than
rounding up.

Date of this pass: **2026-08-23** (session S6). Commands are run from the
repository root.

**Verdict: not green.** Eleven of the thirteen items are done and evidenced.
Two are open, and one of them is a hard blocker for a first external user:
there is **no email provider**, so no external person can sign in. See
§Blockers at the end. Everything else is in place and waiting on those two.

---

## 1. DPA template

**Done.** `compliance/dpa-template.md` — controller/processor split,
sub-processor authorisation with a 30-day objection window, the international
transfer named explicitly (the assistant, and only the assistant), a 48-hour
breach clause, deletion and return, and two annexes describing the actual
processing and the actual measures.

Not signed by anyone, and not reviewed by a lawyer. A consumer beta user does
not sign a DPA — the Privacy Policy is their disclosure — so this is for the
first business customer, which ADR-0027 puts after the consumer validation.

> ADR-0026 also flags **one hour with an Italian fintech lawyer before public
> launch, not before beta**. Still outstanding, still correctly scheduled.
> **Owner: Luca.**

## 2. Subprocessor list published on the privacy page

**Done, with one hole that is item 13.** `compliance/subprocessors.md` names
Hetzner (hosting and object storage), OpenRouter (routing), Google (the
model), Sentry (errors, EU region), Grafana Labs (metrics, EU region), and
records the four vendors that are deliberately **absent**: no managed database,
**no speech-recognition vendor** (D-MLP-45: mobile is on-device only, web is
on-device or off), no LLM-observability platform (SPEC §11 records the
adoption trigger), and no bank aggregator (ADR-0026).

Published in the app: `apps/client/src/screens/SettingsScreen.tsx` renders the
list under Privacy. S4 deliberately left that section named and empty
(D-MLP-71) rather than inventing vendor names; S6 filled it.

Evidence that the list is complete rather than plausible:

```bash
uv run pytest apps/service/tests/test_compliance.py -q
```

`test_every_external_host_the_code_talks_to_is_on_the_list` greps the service
and client source for outbound hostnames and fails on one the page does not
name. A subprocessor list is a claim about the code, so it is checked against
the code.

## 3. Privacy policy

**Done.** `compliance/privacy-policy.md`. States what is held (including the
raw model payloads, which are the easiest thing to omit), the retention of
each, the deletion cascade, the export, EU residency, the single transfer
outside the EU and why, the on-device dictation rule, and the GDPR rights with
the Garante named.

The retention numbers in it are not prose: `test_compliance.py` reads the page
and compares every stated period against the running service's own settings.
The policy and the code cannot drift apart.

## 4. Terms of service

**Done.** `compliance/terms-of-service.md`. Beta with no warranty, an explicit
"this is not financial advice" clause matching ADR-0015's scope (the assistant
is a command interpreter, not an adviser), the SPEC §8 fair-use limits stated
as the sentences the user actually sees, export-and-delete rights, Italian
law.

## 5. Account deletion

**Done, and verified rather than asserted.**

```bash
uv run pytest apps/service/tests/test_deletion_erases.py -q     # 6 passed
```

`DELETE /me` revokes every session, removes the book directory from the
volume, and deletes every Postgres row — `turns` and `llm_calls` included.
The test seeds **every table the schema has** and then asserts the database is
empty by walking `metadata.sorted_tables`, rather than by checking the tables
somebody thought of. That is what caught the one real defect in this area:
`login_tokens` keys on the email rather than on a user, so no cascade reached
it and an unconsumed sign-in link left the address in the database after the
account was erased (D-MLP-100). It is now deleted by address.

`users.deleted_at` is gone (D-MLP-98, migration `0002_retention.sql`): the
deletion is hard, so a column nothing can ever set was a promise the schema
made and the code did not keep.

**Backups within 30 days** — see item 8.

## 6. Data export

**Done.** `GET /me/export` returns one archive: every row about the account,
plus the book directory itself — the YAML revisions and the ledger, in the
format the engine stores, not a summary. Credentials (session and token
hashes) are excluded, because exporting them would widen the blast radius of a
leaked archive.

```bash
uv run pytest apps/service/tests/test_me.py apps/service/tests/test_deletion_erases.py -q
```

`test_export_carries_the_account_before_it_is_erased` checks the archive
against the same fully-seeded account the deletion test uses, so the export is
proved to contain what the deletion is proved to remove.

## 7. EU-region hosting and storage

**Configured and documented; not deployed.** `ops/DEPLOY.md` §1 creates the VM
in `nbg1` (Nuremberg), with a firewall opening 22, 80 and 443 and nothing
else. Object storage is Hetzner's, EU. Sentry and Grafana Cloud are configured
to EU regions in `ops/env.example`.

**Not executed: no Hetzner VM was created.** `hcloud` is installed on this
machine and authenticated against a project belonging to the operator's
business, holding one unrelated production server. Creating billable
infrastructure there was not this session's to do, and every credential the
stack needs beyond Hetzner — DNS, object storage, a mail key, Grafana Cloud —
is absent, so a VM would have been a VM and not a deployment. What was done
instead: the entire stack was **run on this machine from the same compose
files**, migrated, authenticated, and driven through a real import (D-MLP-112).
**Owner: Luca**, one command, `ops/DEPLOY.md` §1.

## 8. Backups, encryption at rest, and the 30-day deletion window

**Done, and the restore was executed successfully.**

```bash
uv run pytest apps/service/tests/test_backup_restore_drill.py -m drill -q   # 3 passed
```

The drill builds the production backup image, starts MinIO (a real
S3-compatible store answering the same `aws s3` calls Hetzner Object Storage
will) and two Postgres containers, backs up two real books, **deletes the
books directory**, restores, and compares every closing balance — through the
service's own money serializer, string for string — plus the item set, the
ledger rows and the whole revision list. A checksum proves a copy; the figures
prove a book.

**Encryption at rest** has two halves and they are different mechanisms, so
they are stated separately rather than as one claim:

- *The volume*: Hetzner encrypts its block storage at rest. This is the host's
  property, relied upon, not something CashKit does.
- *The backups*: sealed with `age` to a **public key before they leave the
  container**, so the backup sidecar can write a backup it cannot read. The
  drill proves it by trying — it downloads a stored object, asserts it begins
  `age-encryption.org/v1` and not `PGDMP`, and asserts `age -d` without the
  identity fails.

**Deletion reaching backups within 30 days** is the item that needed a design
rather than a setting (D-MLP-99). A hard delete destroys the row the
obligation was attached to, so a content-free `deletions` receipt carries it.
The window is closed against **the timestamp of the oldest object still in the
bucket** — every retained backup was written after that instant, so an account
deleted before it cannot be in any of them. That is a checkable statement
about the bucket rather than an assertion about the calendar, and
`cashkit_deletion_backup_windows_overdue` is an alarm for when it stops being
true.

## 9. Zero-retention model routing — **verified as far as it can be**

`provider: {"data_collection": "deny"}` travels on **every** request the
service builds, rather than relying on an account setting, so a
newly-provisioned key cannot silently opt in (D-MLP-31, `test_transport.py`).

Verified live against OpenRouter on 2026-08-23, and the result is more
qualified than "verified" would suggest:

| Check | Result |
|---|---|
| The flag is in the payload of every call | **Yes** — asserted in `test_transport.py`, and re-asserted end to end in `trials/t19_zero_retention.py` |
| A real call with the flag succeeds on the pinned model | **Yes** — served by provider `Google` (Vertex) |
| The router **reads and acts on** the `provider` block, rather than ignoring an unknown field | **Yes**, by a controlled negative: the same request with `provider.only: ["Google Vertex"]` was refused with *"No allowed providers are available… your request's provider.only preference permits only…"*. The router parses this object and enforces it |
| Every endpoint serving `google/gemini-3.7-flash` is Google's own | **Yes** — `Google` and `Google AI Studio`; no third-party reseller is in the path |
| `data_collection: "deny"` demonstrably **excludes** a specific data-collecting endpoint | **Could not be tested.** OpenRouter's public API no longer exposes a per-endpoint data policy (`/models/{id}/endpoints` has no such field, and `/providers` returns only names, URLs and datacentres). No `:free` endpoint — the tier where prompt logging lives — is reachable on this key, so there is no differential control to run the flag against |

**What that means, stated plainly.** The zero-retention claim rests on
OpenRouter's contractual behaviour and Google's, not on a check we can run
from outside. What we *can* prove is that we send the instruction on every
call and that the router acts on the object containing it.

**Owner: Luca.** Confirm zero-retention in writing with OpenRouter before the
first external user, and keep the reply. **Trigger to re-test:** any model or
provider change, which reruns the trial suite anyway (ADR-0028).

## 10. Log retention stated in the privacy policy

**Done, and the statement is tied to the code.** The policy states 30 days for
raw model payloads, 90 for request logs, 30 for backups.
`test_compliance.py::test_the_policy_states_the_retention_the_service_enforces`
reads those numbers out of the page and compares them with `Settings`. Neither
can move alone.

## 11. Log purge jobs verified

**Done.**

```bash
uv run pytest apps/service/tests/test_retention.py -q            # 12 passed
uv run pytest apps/service/tests/test_request_log.py -q          # 7 passed
```

Every job is driven on a frozen clock against genuinely old rows and files
(D-MLP-12 makes the clock a dependency, so this is a test and not a wait), and
each is checked **in both directions** — what must go goes, what must stay
stays. A purge that deletes everything passes a naive test perfectly.

Two things this found. `llm_calls.request` blanked with a Python `None` is the
JSON value `null` — a stored document, not an absent one — so the job
re-counted the same rows for ever and the purge metric showed a backlog it had
already cleared (D-MLP-101). And retention on a rotated log file decides on the
file's **date suffix**, not its mtime, because a restore rewrites every mtime.

## 12. Metrics and logs carry no user identifier

**Done, mechanically.** SPEC §11's hard rule is a closed vocabulary rather
than a convention: every metric declares the complete set of values each label
may take, and an undeclared value **raises** (D-MLP-116). The request log is
built from a fixed six-field list and its route is the matched template, never
the requested path.

```bash
uv run pytest apps/service/tests/test_metrics.py apps/service/tests/test_request_log.py -q
```

Both are checked by scanning the real output after a realistic walk of the API
— auth, a read, a card and its confirmation — for a uuid or an `@`.

Sentry is configured so it *cannot* carry the payload
(`send_default_pii=False`, `include_local_variables=False`,
`max_request_body_size="never"`, no traces) and a `before_send` scrubs what
gets through anyway (D-MLP-122). The `request_id` tag survives, because it is
the point.

## 13. Email provider — **OPEN, and a blocker**

**Not done.** No provider is chosen and no key exists. `ConsoleMailer` prints
the sign-in link to the container log, which is fine for a development run and
means **no external person can sign in at all**.

Three things happen together when a provider is picked, and none of them can
be skipped:

1. the key goes in the env file (`MAIL_PROVIDER_API_KEY`, already wired
   through `ops/docker-compose.prod.yml`);
2. a `Mailer` implementation is written — `cashkit_service.mail.Mailer` is a
   protocol with two implementations already, so this is one class;
3. **the provider joins `compliance/subprocessors.md`**, because it will hold
   the user's address and the sign-in link.

**Owner: Luca** (the vendor choice is his; EU-hosted with a DPA is the
constraint that matters). Until then this checklist is not green, and SPEC §9
says that means no external user.

---

## Blockers

| # | Item | Why it blocks | Owner |
|---|---|---|---|
| 13 | No email provider | Nobody outside the team can sign in, and the subprocessor list is incomplete until the vendor is named | **Luca** |
| 7 | Nothing is deployed | There is no EU host serving anything yet, so there is nothing for an external user to reach. One `hcloud server create` plus `ops/DEPLOY.md` | **Luca** |

## Not blockers, but do them before public launch

| Item | Owner |
|---|---|
| One hour with an Italian fintech lawyer (ADR-0026) | Luca |
| Written zero-retention confirmation from OpenRouter (item 9) | Luca |
| Final copy review of refusal and clarification text (SPEC §13, ADR-0025) | Luca |

## Reproducing this whole page

```bash
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
uv run pytest apps/service/tests apps/service/trials -q              # the per-commit suite
uv run pytest apps/service/tests -m drill -q                         # restore, streaming, alarms
uv run pytest apps/service/trials/t19_zero_retention.py -m live_model -q
```
