"""Naive Decimal reference engine — the oracle. Kept forever, never deleted.

``cashkit.reference.run(book)`` evaluates a book the slow, obvious way: one
period, one item, one ``Decimal`` at a time. It is not a development scaffold —
it is the artifact the vectorized engine is tested against on every run, and the
Definition of Done requires it to still exist and still agree.
"""

from .engine import run

__all__ = ["run"]
