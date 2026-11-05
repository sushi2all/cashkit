"""A seeded small-company book shared by scenario, query and workflow families.

``Company`` carries the setup source *and* an independent per-item cash model
of that same book, so a family can state "the downside closes at X" by
recomputing the flows it changed — never by running the engine.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..oracle import ZERO, add_months, balances, eur, long_date, money, month_name, monthly_occurrences, total
from ._common import IMPORTS, BookSpec, D, create_code, dt, money_draw, pick


@dataclass
class Company:
    spec: BookSpec
    price: Decimal
    units: Decimal
    acme: Decimal
    beta: Decimal
    alpha: Decimal
    alpha_start: date
    alpha_end: date  # exclusive
    payroll: Decimal
    contractor: Decimal
    rent: Decimal
    body: str = ""
    events: list[tuple[str, date, Decimal]] = field(default_factory=list)

    @property
    def periods(self) -> list[date]:
        return self.spec.periods

    # -- the independent model ------------------------------------------- #

    def flows(self, *, units: Decimal | None = None, acme_factor: Decimal = Decimal(1), alpha_shift: int = 0,
              drop: tuple[str, ...] = (), extra: dict[str, dict[date, Decimal]] | None = None) -> dict[str, dict[date, Decimal]]:
        end = self.spec.end
        units = self.units if units is None else units
        months = monthly_occurrences(self.spec.start, None, end)
        out = {
            "product": {d: money(self.price * units) for d in months},
            "acme": {d: money(self.acme * acme_factor) for d in months},
            "beta": {d: self.beta for d in months},
            "alpha_project": {
                d: self.alpha
                for d in monthly_occurrences(add_months(self.alpha_start, alpha_shift),
                                             add_months(self.alpha_end, alpha_shift), end)
            },
            "payroll": {d: -self.payroll for d in months},
            "contractor": {d: -self.contractor for d in months},
            "rent": {d: -self.rent for d in months},
        }
        for name in drop:
            out.pop(name, None)
        out.update(extra or {})
        return out

    def net(self, flows: dict[str, dict[date, Decimal]]) -> dict[date, Decimal]:
        net: dict[date, Decimal] = {}
        for item in flows.values():
            for d, v in item.items():
                net[d] = net.get(d, ZERO) + v
        for _id, day, amount in self.events:
            key = date(day.year, day.month, 1)
            net[key] = net.get(key, ZERO) + amount
        return net

    def closing(self, flows=None, opening: Decimal | None = None) -> Decimal:
        flows = self.flows() if flows is None else flows
        return (self.spec.opening if opening is None else opening) + total(self.net(flows))

    def balances(self, flows=None, opening: Decimal | None = None) -> dict[date, Decimal]:
        flows = self.flows() if flows is None else flows
        return balances(self.spec.opening if opening is None else opening, self.periods, self.net(flows))

    # -- prompt text ------------------------------------------------------ #

    def description(self) -> str:
        s = self.spec
        lines = [
            f"The book `{s.id}` exists at `ROOT` (open in `kit`): monthly grain, horizon {long_date(s.start)} to "
            f"{long_date(s.end)} (exclusive), opening balance {eur(s.opening)}. Base scenario `base`:",
            f"- params `price` = {self.price}, `units` = {self.units}",
            f"- `product` (derived, inflow): `p.price * p.units` every month — tags cat=revenue",
            f"- `acme` {eur(self.acme)}/month inflow — tags customer=acme, cat=revenue",
            f"- `beta` {eur(self.beta)}/month inflow — tags customer=beta, cat=revenue",
            f"- `alpha_project` {eur(self.alpha)}/month inflow from {month_name(self.alpha_start)} to "
            f"{month_name(add_months(self.alpha_end, -1))} inclusive — tags project=alpha, cat=revenue",
            f"- `payroll` {eur(self.payroll)}/month outflow — tags cat=payroll",
            f"- `contractor` {eur(self.contractor)}/month outflow — tags cat=payroll",
            f"- `rent` {eur(self.rent)}/month outflow — tags cat=premises",
        ]
        for eid, day, amount in self.events:
            lines.append(f"- ledger event `{eid}`: {eur(amount)} on {long_date(day)} (committed)")
        lines.append("All flows land at the start of each month and settle immediately.")
        return "\n".join(lines)


def company(rng: random.Random, *, burn: bool = False, dip: bool = False) -> Company:
    """Draw a company. ``burn`` makes it run out of cash inside the horizon;
    ``dip`` adds one large committed payment so the balance troughs and recovers."""
    y = pick(rng, 2026, 2027)
    spec = BookSpec(f"company_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 20_000, 80_000, 1_000))
    c = Company(
        spec=spec,
        price=money_draw(rng, 20, 90, 5),
        units=Decimal(rng.randrange(40, 160, 10)),
        acme=money_draw(rng, 3_000, 9_000, 100),
        beta=money_draw(rng, 1_000, 5_000, 100),
        alpha=money_draw(rng, 2_000, 7_000, 250),
        alpha_start=date(y, rng.randrange(2, 6), 1),
        alpha_end=date(y, rng.randrange(8, 12), 1),
        payroll=money_draw(rng, 6_000, 14_000, 500),
        contractor=money_draw(rng, 1_500, 4_000, 250),
        rent=money_draw(rng, 1_000, 3_000, 100),
    )
    if burn:
        # Make monthly net clearly negative and the opening small enough to run out.
        c.payroll += money_draw(rng, 12_000, 20_000, 500)
        spec = BookSpec(spec.id, spec.start, spec.end, money_draw(rng, 15_000, 40_000, 1_000))
        c.spec = spec
    if dip:
        day = date(y, rng.randrange(4, 9), 10)
        c.events.append(("tax_settlement", day, -money_draw(rng, 40_000, 90_000, 1_000)))
    c.body = _body(c)
    return c


def _item(item_id: str, name: str, amount: Decimal, start: date, tags: dict[str, str], end: date | None = None) -> str:
    direction = "in" if amount > 0 else "out"
    end_src = "None" if end is None else dt(end)
    return (
        f'kit.set_item(Item(id="{item_id}", name="{name}", kind="flow", direction="{direction}", tags={tags!r},\n'
        f"    segments=[Segment(start={dt(start)}, end={end_src}, recurrence=monthly,\n"
        f"                      amount=Amount(constant={D(amount)}))]))\n"
    )


def _body(c: Company) -> str:
    s = c.spec.start
    parts = [
        "monthly = Recurrence(every=1, unit=Grain.MONTH)\n",
        f'kit.set_param("price", {D(c.price)})\n',
        f'kit.set_param("units", {D(c.units)})\n',
        'kit.set_item(Item(id="product", name="Product sales", kind="derived", direction="in",\n'
        '    tags={"cat": "revenue"}, formula="p.price * p.units"))\n',
        _item("acme", "Acme", c.acme, s, {"customer": "acme", "cat": "revenue"}),
        _item("beta", "Beta", c.beta, s, {"customer": "beta", "cat": "revenue"}),
        _item("alpha_project", "Project Alpha", c.alpha, c.alpha_start, {"project": "alpha", "cat": "revenue"}, c.alpha_end),
        _item("payroll", "Payroll", -c.payroll, s, {"cat": "payroll"}),
        _item("contractor", "Contractor", -c.contractor, s, {"cat": "payroll"}),
        _item("rent", "Rent", -c.rent, s, {"cat": "premises"}),
    ]
    for eid, day, amount in c.events:
        parts.append(f'kit.add_event(Event(id="{eid}", date={dt(day)}, amount={D(amount)}, status="committed"))\n')
    return "".join(parts)


def setup(c: Company, extra: str = "") -> str:
    return IMPORTS + "\n" + create_code(c.spec) + "\n" + c.body + ("\n" + extra if extra else "")
