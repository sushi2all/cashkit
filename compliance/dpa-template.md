# Data Processing Agreement — template

**Status:** template. **Not legal advice.** ADR-0026 statement 4 requires a DPA
to exist before a hosted or LLM-touching feature ships, and ADR-0026 also
flags one hour with an Italian fintech lawyer **before public launch** — this
document is what that hour reviews, not a substitute for it.

**When this is used.** A consumer beta user does not sign a DPA: for a
consumer, the Privacy Policy is the disclosure and the controller/processor
split is described there. This template is for the case ADR-0027 puts next —
a business customer, where the customer is the controller and needs a written
agreement naming what we do with their data.

Placeholders are in `{{ }}`.

---

## Data Processing Agreement

Between **{{ CUSTOMER LEGAL NAME }}**, {{ address }} (the **Controller**)
and **{{ PROCESSOR LEGAL NAME }}**, {{ address }} (the **Processor**),
supplementing the agreement under which the Processor provides CashKit
(the **Service**).

### 1. Roles

The Controller determines the purposes and means of processing the Personal
Data described in Annex 1. The Processor processes it only on the Controller's
documented instructions, which the agreement and this DPA constitute.

### 2. Subject matter and duration

Processing lasts for the term of the agreement and for the retention periods
in Annex 1, whichever is longer. On termination the Processor deletes the
Personal Data in accordance with clause 9.

### 3. The Processor's obligations

The Processor shall:

a. process Personal Data only on documented instructions, including for
   transfers to a third country, unless required otherwise by Union or Member
   State law, in which case it informs the Controller first unless that law
   forbids it;
b. ensure that persons authorised to process it are bound by confidentiality;
c. take the technical and organisational measures in Annex 2;
d. respect the conditions in clause 4 for engaging a sub-processor;
e. assist the Controller, by appropriate technical and organisational
   measures, in responding to data-subject requests — Annex 2 notes that
   export and deletion are self-service functions of the Service;
f. assist the Controller with Articles 32 to 36 GDPR;
g. at the Controller's choice, delete or return the Personal Data at the end
   of the service, and delete existing copies unless law requires storage;
h. make available the information needed to demonstrate compliance with
   Article 28 and allow and contribute to audits, on reasonable notice and no
   more than once a year unless a Supervisory Authority requires otherwise.

### 4. Sub-processors

The Controller gives general written authorisation for the sub-processors
listed at `{{ SUBPROCESSORS URL }}` (see `compliance/subprocessors.md`). The
Processor shall give **{{ 30 }} days' notice** before adding or replacing one,
during which the Controller may object on reasonable data-protection grounds;
if the objection cannot be resolved, either party may terminate the affected
part of the Service.

Each sub-processor is bound by data-protection obligations no less protective
than these, and the Processor remains fully liable for their performance.

### 5. International transfers

The Service is hosted in the European Union. **One category of processing
leaves the EU**: the assistant. The content of an assistant request — the
user's instruction and a compact summary of their book — is transmitted to
{{ OpenRouter, Inc. }} (United States) and from there to the model provider.
That transfer relies on the Standard Contractual Clauses in the sub-processor
agreements, and every request carries a zero-data-collection instruction so
the payload is not retained by, or used to train, the receiving service.

If the Controller does not accept this transfer, the assistant must be
disabled for its users; every other function of the Service operates without
it.

### 6. Security

Annex 2. In summary: encryption in transit, encryption at rest for the volume
and separately for backups, one-time sign-in links with no stored password,
least-privilege separation between the application and the backup process, and
retention limits enforced by scheduled jobs rather than by policy alone.

### 7. Personal data breach

The Processor notifies the Controller **without undue delay and in any case
within 48 hours** of becoming aware of a personal data breach, with the
information available at the time, and updates as more is known.

### 8. Data protection impact assessment

The Processor provides reasonable assistance with a DPIA and with prior
consultation of a Supervisory Authority.

### 9. Deletion and return

On termination, and at the Controller's choice, the Processor deletes or
returns the Personal Data within {{ 30 }} days. Deletion of live data is
immediate; **backups holding the data are deleted within 30 days** as the
backup retention window rotates, and the Processor keeps a record that the
window closed.

### 10. Liability and precedence

Liability follows the main agreement. Where this DPA conflicts with the main
agreement on the processing of Personal Data, this DPA prevails.

---

## Annex 1 — the processing

| | |
|---|---|
| **Subject matter** | Providing a cash-flow modelling service |
| **Duration** | Term of the agreement, plus the retention periods below |
| **Nature and purpose** | Storing, computing on, and displaying the Controller's financial model; interpreting natural-language instructions into operations on it |
| **Categories of data subject** | The Controller's authorised users |
| **Categories of personal data** | Email address; session records; the content of the book (items, amounts, dates, categories, notes, recorded actuals, and every saved revision); the text of assistant conversations; the raw model request and reply payloads; technical request logs |
| **Special categories** | Not requested. The Controller should be aware that free-text notes and category names can imply them (a payee can reveal health, religion or trade-union membership) and should instruct its users accordingly |
| **Retention** | Book and account: until deleted. Raw model payloads: **30 days**. Request logs: **90 days**. Backups: **30 days**. Sign-in tokens: swept a day after expiry |

## Annex 2 — technical and organisational measures

1. **Hosting** in the European Union; a single-tenant virtual machine with
   provider-side encryption at rest.
2. **Transport** over TLS, with HSTS, on a proxy that publishes no other port.
3. **Backups** nightly, encrypted with a public key **before** upload, so the
   process that writes them cannot read them; retained 30 days; the restore
   procedure is documented and has been executed successfully.
4. **Authentication** by one-time link, fifteen-minute validity, single use,
   with no stored password. `DELETE /me` revokes every session.
5. **Separation** between the application and the backup process: separate
   images, separate credentials, and the books mounted read-only for backup.
6. **Retention** enforced by scheduled jobs with tests, not by policy alone.
7. **Logging** with no user identifier: request logs carry a route template
   and a request identifier; metrics carry a closed set of label values that
   contains no user, book or session field.
8. **Error tracking** configured not to collect request bodies or local
   variables, with a scrubber that removes payload fields if present.
9. **Data-subject self-service**: export and deletion are functions of the
   Service and need no request to the Processor.
10. **Alerting** hosted off the machine it monitors, covering availability,
    spend, backup freshness, disk, and the deletion-to-backup window.
