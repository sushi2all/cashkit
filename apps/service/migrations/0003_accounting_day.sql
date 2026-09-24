-- The default day of the month an authored line falls on (S7).
--
-- A recurring line needs a day: `Segment.start` is a date, and the day sets the
-- recurrence phase, the escalation anniversary and — through settlement — which
-- month the cash actually lands in. Until now the assistant had to pick one
-- whenever the user did not say, and it picked silently.
--
-- So the book carries the answer instead. It is a *setting* and not a proposal:
-- it changes no figure that already exists, only the day a future line is
-- authored on when the user did not name one.
--
-- Text rather than a number, because there are two kinds of answer and only one
-- of them is a number: a fixed day (1..28) or `eom`, the last day of whichever
-- month the occurrence falls in — 28, 29, 30 or 31. `eom` is not a day at all,
-- it is the engine's own recurrence anchor (`Recurrence.anchor = "eom"`), so
-- storing it as 31 and clamping would be a different rule wearing its clothes.
--
-- A fixed day stops at 28 on purpose. A month step clamps to month end, so a
-- default of the 30th would mean the 28th every February and the 30th otherwise
-- — a default that quietly changes meaning. A user who wants the last day of
-- every month asks for it by name, and gets the anchor that means it.
ALTER TABLE books
    ADD COLUMN accounting_day text NOT NULL DEFAULT '1'
        CHECK (accounting_day = 'eom' OR accounting_day ~ '^([1-9]|1[0-9]|2[0-8])$');
