"""The workbooks T16 imports, built rather than committed.

Two shapes, both from `proto/TESTLOG.md`:

* **T06** is a round-trip of our own export, and the trial gets that workbook
  by calling ``GET /export`` — building a look-alike here would test the
  look-alike. :func:`export_like` exists only for the scripted tests, which
  need a file without a live service behind it.
* **T07** is the messy human sheet: month-name headers, POSITIVE expenses,
  section and SUM rows, a starting-balance corner cell, a 13th-month salary,
  bimonthly utilities, one annual premium and a mid-year price rise. The
  figures are S2's ``trials/t07_messy_budget_sheet.py`` verbatim, so both
  trials assert the same closing balances and a difference between them is a
  difference in the pipeline, not in the fixture.

Generated, not committed, because a binary in the repository is a fixture
nobody can read in a diff.
"""

from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal

from openpyxl import Workbook


def with_cached_values(data: bytes, *, sheet: str, cached: dict[str, float]) -> bytes:  # noqa: ARG001
    """Give a generated workbook the cached formula values Excel would store.

    openpyxl writes ``<f>SUM(B5:B6)</f>`` and no ``<v>``, so a ``data_only``
    read of a formula cell comes back empty. A workbook a person saved from
    Excel carries both, and the import loop reads both — the value to reconcile
    against and the formula to recognise a subtotal row by. Without this the
    fixture would be testing a simplification of the thing it is for.
    """
    source = zipfile.ZipFile(io.BytesIO(data))
    order = sorted(n for n in source.namelist() if n.startswith("xl/worksheets/sheet"))
    # The builders write to the first worksheet; `sheet` names it for the reader.
    target = order[0] if order else None
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as written:
        for item in source.infolist():
            body = source.read(item.filename)
            if item.filename == target:
                body = _inject(body.decode("utf-8"), cached).encode("utf-8")
            written.writestr(item, body)
    source.close()
    return out.getvalue()


#: openpyxl writes a formula cell as ``<c r="B7"><f>SUM(B5:B6)</f><v /></c>``:
#: the formula, and an empty cached value. Filling that ``<v />`` is all it
#: takes to make the file read the way one Excel saved does.
_FORMULA_CELL = re.compile(
    r'<c(?P<attrs>[^>]*?)r="(?P<ref>[A-Z]+\d+)"(?P<rest>[^>]*)>'
    r"\s*<f>(?P<f>[^<]*)</f>\s*<v\s*/>\s*</c>"
)


def _inject(xml: str, cached: dict[str, float]) -> str:
    def replace(match: re.Match[str]) -> str:
        ref = match.group("ref")
        if ref not in cached:
            return match.group(0)
        attrs = (match.group("attrs") + match.group("rest")).replace('t="str"', "")
        return (
            f'<c{attrs}r="{ref}"><f>{match.group("f")}</f>'
            f"<v>{cached[ref]!r}</v></c>"
        )

    return _FORMULA_CELL.sub(replace, xml)


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# --- T07, the messy family budget (proto T07, S2's trial figures) ---------- #

OPENING = Decimal("3200")
SALARY = Decimal("2800")
BONUS = Decimal("2800")
MORTGAGE = Decimal("1150")
UTILITIES = Decimal("240")
NURSERY_BEFORE = Decimal("420")
NURSERY_AFTER = Decimal("480")
INSURANCE = Decimal("960")

#: Utilities land in the odd months; the nursery rises from July; the insurance
#: premium is paid once, in June; the 13th month arrives in December.
SALARY_ROW = [SALARY] * 12
BONUS_ROW = [None] * 11 + [BONUS]
MORTGAGE_ROW = [MORTGAGE] * 12
UTILITIES_ROW = [UTILITIES if i % 2 == 0 else None for i in range(12)]
NURSERY_ROW = [NURSERY_BEFORE] * 6 + [NURSERY_AFTER] * 6
INSURANCE_ROW = [None] * 5 + [INSURANCE] + [None] * 6


def _net() -> list[Decimal]:
    out: list[Decimal] = []
    for index in range(12):
        income = (SALARY_ROW[index] or 0) + (BONUS_ROW[index] or 0)
        spend = (
            (MORTGAGE_ROW[index] or 0)
            + (UTILITIES_ROW[index] or 0)
            + (NURSERY_ROW[index] or 0)
            + (INSURANCE_ROW[index] or 0)
        )
        out.append(Decimal(income) - Decimal(spend))
    return out


def expected_closings() -> list[Decimal]:
    """The twelve closing balances the sheet itself implies."""
    balance = OPENING
    series: list[Decimal] = []
    for net in _net():
        balance += net
        series.append(balance)
    return series


def _totals(rows: list[list[Decimal | None]]) -> list[Decimal]:
    return [
        sum((Decimal(row[i]) for row in rows if row[i] is not None), Decimal(0))
        for i in range(12)
    ]


def messy_family_budget() -> bytes:
    """The T07 workbook: the actual "initialize from an existing budget" case."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Budget 2026"

    sheet["A1"] = "Starting balance"
    sheet["B1"] = float(OPENING)

    sheet.append([])
    sheet.append(["", *MONTHS])          # row 3: the month header
    sheet.append(["INCOME"])             # row 4
    sheet.append(["Salary", *[float(v) for v in SALARY_ROW]])       # row 5
    sheet.append(["Bonus (13th month)", *[None if v is None else float(v) for v in BONUS_ROW]])
    sheet.append(
        ["Total in", *[float(v) for v in _totals([SALARY_ROW, BONUS_ROW])]]
    )                                     # row 7
    sheet.append([])
    sheet.append(["EXPENSES"])           # row 9
    sheet.append(["Mortgage", *[float(v) for v in MORTGAGE_ROW]])
    sheet.append(["Utilities (every 2 months)", *[None if v is None else float(v) for v in UTILITIES_ROW]])
    sheet.append(["Nursery", *[float(v) for v in NURSERY_ROW]])
    sheet.append(["Insurance (annual premium)", *[None if v is None else float(v) for v in INSURANCE_ROW]])
    sheet.append(
        [
            "Total out",
            *[
                float(v)
                for v in _totals([MORTGAGE_ROW, UTILITIES_ROW, NURSERY_ROW, INSURANCE_ROW])
            ],
        ]
    )                                     # row 14
    sheet.append([])
    net = _net()
    sheet.append(["Net", *[float(v) for v in net]])                 # row 16
    sheet.append(["Closing balance", *[float(v) for v in expected_closings()]])  # row 17

    # The sheet's own arithmetic, as formulas as well as values, because that is
    # what tells a reader (and the loop's candidate scan) that a row is a total.
    total_in = _totals([SALARY_ROW, BONUS_ROW])
    total_out = _totals([MORTGAGE_ROW, UTILITIES_ROW, NURSERY_ROW, INSURANCE_ROW])
    closing = expected_closings()
    cached: dict[str, float] = {}
    for index in range(12):
        column = chr(ord("B") + index)
        sheet[f"{column}7"] = f"=SUM({column}5:{column}6)"
        sheet[f"{column}14"] = f"=SUM({column}10:{column}13)"
        sheet[f"{column}16"] = f"={column}7-{column}14"
        previous = "B1" if index == 0 else f"{chr(ord('B') + index - 1)}17"
        sheet[f"{column}17"] = f"={previous}+{column}16"
        cached[f"{column}7"] = float(total_in[index])
        cached[f"{column}14"] = float(total_out[index])
        cached[f"{column}16"] = float(net[index])
        cached[f"{column}17"] = float(closing[index])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return with_cached_values(buffer.getvalue(), sheet="Budget 2026", cached=cached)


# --- a stand-in for our own export, for the scripted tests ---------------- #


def export_like(
    *,
    opening: Decimal = Decimal("2500.00"),
    rows: list[tuple[str, str, list[Decimal]]] | None = None,
    months: list[str] | None = None,
) -> bytes:
    """A workbook in the shape ``GET /export`` produces (`routers/export.py`).

    The ``Opening balance | meta`` row is the self-describing part proto T06
    found to be the difference between a round-trip that works and one no model
    could recover.
    """
    months = months or [f"2026-{m:02d}" for m in range(1, 13)]
    rows = rows or [("Salary", "flow", [Decimal("2000")] * 12)]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Budget"
    sheet.append(["Item", "Kind", *months])
    sheet.append(["Opening balance", "meta", float(opening)])
    for name, kind, values in rows:
        sheet.append([name, kind, *[float(v) for v in values]])
    sheet.append([])
    balance = opening
    closing: list[float] = []
    for index in range(len(months)):
        balance += sum((row[2][index] for row in rows), Decimal(0))
        closing.append(float(balance))
    sheet.append(["Closing balance", "", *closing])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


__all__ = [
    "MONTHS",
    "OPENING",
    "expected_closings",
    "export_like",
    "messy_family_budget",
]
