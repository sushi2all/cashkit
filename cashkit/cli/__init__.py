"""Command-line interface (PRD §8.3, §8.4, §8.6).

``cashkit init``, ``doctor``, ``validate``, ``run``, ``status``, ``commit``,
``history``, ``describe``, ``serve --quack``. Every command wraps the SDK and
accepts ``--json``; the human rendering is a view of the same structure, never a
second story. ``cashkit doctor --json`` is runnable by an agent as its first
action and reports "there is no book here" as an answer rather than a failure.
"""

from .main import EXIT_DIAGNOSTIC, EXIT_OK, EXIT_USAGE, build_parser, main

__all__ = ["EXIT_DIAGNOSTIC", "EXIT_OK", "EXIT_USAGE", "build_parser", "main"]
