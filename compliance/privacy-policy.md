# Privacy Policy

**Last updated:** 2026-08-23 · **Applies to:** the CashKit beta (web and
Android). **Not legal advice**; reviewed by an Italian lawyer before public
launch, not before the beta (ADR-0026).

## Who we are

CashKit is operated by Luca Sorgiacomo (Progress Lab), Italy. Contact:
[luca.sorgiacomo@progresslab.it](mailto:luca.sorgiacomo@progresslab.it).

For the data in your book we are a **processor** and you are the
**controller**: it is your money, described in your words, and we hold it to
run the service you asked for.

## What we hold

**Your account.** Your email address, and the sign-in sessions attached to it.
Nothing else — no name, no phone number, no payment details in the beta.

**Your book.** Everything you enter or import: items, amounts, dates,
categories, notes, recorded actuals and every saved revision of all of it.
This is financial data about you. It lives in one directory per account on our
server, in files the engine reads.

**Your conversations with the assistant.** What you typed, what came back, and
the intents the model produced. Each turn also records which model answered,
how many tokens it used, what it cost and how long it took.

**The raw model requests and replies.** For each model call we store the exact
payload sent and received. This is the only way to work out why the assistant
misread something. It contains a summary of your book.

**Technical logs.** One line per request: an identifier, the route (a template
such as `/proposals/{proposal_id}`, never a URL with your data in it), the
status and the duration. No address, no session token, no body.

We do **not** hold: bank credentials, card details, location, contacts,
advertising identifiers, or a recording of your voice.

## How long we hold it

| Data | Kept for | How it goes |
|---|---|---|
| Your book, your account, your turns | Until you delete your account | You delete it |
| **Raw model requests and replies** | **30 days** | Blanked automatically. The numbers — tokens, cost, latency — are kept so we can see what the service costs |
| **Request logs** | **90 days** | Deleted automatically |
| Sign-in link tokens | 15 minutes to use, swept a day after they expire | Deleted automatically |
| **Backups** | **30 days**, then deleted | Deleted automatically |

Those numbers are not a promise in prose. They are settings the running
service reads, and a test fails if this page and the service disagree.

## Deleting your account

Settings → Delete account, and type the confirmation phrase. When you do:

- every sign-in session is revoked immediately;
- your book directory is removed from the disk immediately;
- every database row belonging to you is deleted immediately — your account,
  your turns, **the raw model requests and replies**, your proposals, your
  imports, and any unused sign-in link issued to your address;
- your data leaves the **backups within 30 days**, as they rotate out.

Deletion is not reversible and there is no recovery window. We do not keep a
marked-as-deleted copy of your account: we keep only a dated record that a
deletion happened and that its backup window closed, and that record contains
no address and nothing you wrote.

## Taking your data with you

Settings → Export my data gives you one archive: every database row about you,
plus your book directory exactly as the engine stores it — the YAML revisions
and the ledger. It is your data in the format we actually keep it in, not a
summary. Sign-in credentials are excluded on purpose; exporting them would
only widen the damage if the archive leaked.

## Where it is, and who else touches it

Everything runs in the **European Union**: a Hetzner server in Germany, with
backups in EU object storage. Error tracking and metrics are configured to EU
regions.

The one part that leaves the EU is the assistant. When you ask it something,
your instruction and a compact summary of your book go to **OpenRouter** (US),
which passes them to **Google's** model. Every one of those requests carries a
zero-data-collection instruction, so the request is not to be kept or trained
on. If you never use the assistant, nothing about your book leaves our own
machines.

The full list of companies involved, and what each can see, is on the
[subprocessors page](subprocessors.md). It is short, and it is the whole list.

## Dictation

Dictation runs **on your device**. On Android the app requires on-device
recognition and will not fall back to a cloud service. On the web it uses your
browser's on-device recognition if your browser has one, and turns dictation
off if it does not — we would rather give you a keyboard than send your voice
somewhere.

## Security

Transport is HTTPS. The disk is encrypted at rest by our host, and backups are
separately encrypted before they are uploaded, with a key that is not kept on
the server — so a copy of our backups is not a copy of your data. Sign-in is a
one-time link with a fifteen-minute life; there is no password to leak.

This is a beta run by one person. We think the above is honest and adequate
for a beta; it is not a claim to have been audited.

## Your rights

Under the GDPR you can ask for access, correction, deletion, restriction,
portability, and to object. Access, deletion and portability are buttons in
the app and are the fastest route. For anything else, write to
[luca.sorgiacomo@progresslab.it](mailto:luca.sorgiacomo@progresslab.it) and we
will answer within 30 days. You can also complain to the Garante per la
protezione dei dati personali (Italy) or to your own supervisory authority.

## Changes

If we change this policy in a way that affects you, we will say so in the app
before it takes effect. The history of this file is in the repository, so the
change itself is visible and dated.
