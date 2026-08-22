"""Service-level trials (SPEC §10).

T01–T12 are the ported proto trials and belong to session S2, which owns the
model layer. S1 owns the three that need no model at all and must therefore
pass with **zero model calls**:

* ``t13_no_unproposed_mutation`` — no path mutates a book without a stored,
  user-accepted proposal, including every SPEC §2.5 staleness path;
* ``t17_correction_scar`` — a correction leaves the original retrievable and
  the note present;
* ``t18_record_actual_discriminator`` — the context flag and date rule.

They run as part of the app suite: ``uv run pytest apps/service/trials -q``.
"""
