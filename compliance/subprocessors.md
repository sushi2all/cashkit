# Subprocessors

**Last updated:** 2026-08-23 · **Status:** current for the beta. This page is
published in the app (Settings → Privacy) and is the list SPEC §9 requires
before any external user.

CashKit is a **processor** for the data in your book. You are the controller.
The companies below process that data on our instructions, and each one is
here because the product cannot work without it.

We will tell you before we add a subprocessor.

| Subprocessor | What it does for CashKit | What it can see | Where |
|---|---|---|---|
| **Hetzner Online GmbH** (Germany) | Hosting: the virtual machine, its disk, and the object storage the backups go to | Everything, at rest. The disk is encrypted at rest by Hetzner; the backups are separately encrypted by us before upload, so Hetzner holds ciphertext for those | Germany / Finland (EU) |
| **OpenRouter, Inc.** (United States) | Routes each assistant request to the model | The text of the request and the reply while it is in flight: your instruction and a compact summary of your book | United States |
| **Google** (Google Ireland Ltd / Google LLC) | The model itself — `google/gemini-3.7-flash`, served by Google Vertex AI or Google AI Studio | The same request and reply | Google's regions |
| **Functional Software, Inc. ("Sentry")** | Error tracking: an unhandled error, with a request identifier | The type of the error and where in our code it happened. **Not** your text, not your figures, not your address — the client is configured not to collect request bodies or local variables, and a scrubber removes them if they appear anyway | EU region (Frankfurt) |
| **Grafana Labs** | Metrics and the uptime check | Counts, timings and costs. No identifier of any kind: the metric labels are a closed list that contains no user, book or session field | EU region |
| *(not yet chosen)* **Email provider** | Sends the sign-in link | Your email address and the link | **Open — see below** |

## Things that are deliberately **not** on this list

**No database vendor.** Postgres runs in a container on our own machine. There
is no managed database service, so there is no additional company holding it.

**No speech-recognition vendor.** Dictation runs on your own device. On mobile
the app requires on-device recognition and has no cloud fallback. On the web it
uses the browser's on-device recognition where the browser offers one, and
**switches dictation off** where it does not, rather than sending your voice to
a service. Turning cloud dictation on would mean adding a vendor to this page
first.

**No LLM-observability platform.** We record every model call in our own
database, with its raw request and reply purged after 30 days. We deliberately
do not send a copy of that to a third-party prompt-analytics product: it would
be a second copy of your financial data on a second retention schedule, for a
tool we do not need yet.

**No bank aggregator.** CashKit has no bank connection. You type or import
your own numbers.

## Open items

- **The email provider is not chosen yet.** Until it is, sign-in links are
  printed to our own server log rather than sent, which means the app is not
  usable by anyone outside the team. **This page must be updated, and this
  sentence removed, before the first external beta user.**

## Contact

Questions about this list, or about anything on it:
[luca.sorgiacomo@progresslab.it](mailto:luca.sorgiacomo@progresslab.it).
