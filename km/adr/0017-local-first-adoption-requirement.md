# ADR-0017 — Local-first is an adoption requirement, with two target configurations

**Date** 2026-08-05 · **Status** accepted · **Type** strategy · **Source** design session 2026-08-05

## Context

ADR-0014 committed the UI to single-user local-first, but framed local execution as a simplification (no auth, no tenancy, no Postgres). It did not treat local model execution as a requirement, and did not contemplate mobile at all.

If the only viable configuration is a frontier API, two segments are excluded outright: privacy-conscious users who will not send financial data off-premises, and any offline use. For an Italian SME tool this is not a niche, it is most of the addressable market.

## Decision

**Local execution is an adoption requirement, not a deployment convenience.** Two target configurations, deliberately kept distinct because they cost very different amounts:

**A. Privacy-conscious host (near-free).** One machine on-premises: SDK, local web app (ADR-0014), and a 20–30B-class open-weight model on the same host. No CashKit changes required. This is a deployment guide and an eval, not architecture.

**B. Offline mobile (a product line).** Engine and model on-device, no network. This is not a configuration of the current stack; it requires an engine runtime that works off CPython-on-a-server (native port or WASM shell) and a ~3–4B-class on-device model. Treated as its own initiative with its own gates, after v1.

Frontier API remains a supported and probably default configuration for users who don't need either.

## Alternatives considered

- **Frontier-only for v1, revisit later**: rejected. The decisions that determine whether a small model can drive CashKit are made in the SDK surface now (ADR-0019); deferring the requirement means paying to unpick it later.
- **Treat privacy and mobile as one "local" workstream**: rejected. Conflating a config decision with an engine port hides an order-of-magnitude cost difference.

## Risks and implications

- Configuration B is the one that bites. `pygit2`, `duckdb` and CPython are all obstacles; see [[0018-revision-store-is-an-interface]] for the piece that must be decided before it hardens.
- Local inference breaks the PRD §8.1 property "no system services, everything is a file". Configuration A trades that for an ops burden (drivers, weights, quantization) that becomes a support cost. Accepted knowingly.
- Claims about local model viability must be backed by a scored eval per model, not asserted. Building that harness is prerequisite work, not a nice-to-have.

Related: [[0014-ui-delivery-post-v1]], [[0016-engine-and-sdk-are-model-free]], [[0018-revision-store-is-an-interface]], [[0019-intent-grammar-agent-surface]]
