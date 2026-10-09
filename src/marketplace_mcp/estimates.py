"""Decimal estimates are partial observations, never inventory or order promises."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def validate_quantities(urls, quantities, units):
    if quantities is None:
        if units is not None:
            raise ValueError
        return None
    if not isinstance(quantities, list) or len(quantities) != len(urls):
        raise ValueError
    units = ["unit"] * len(urls) if units is None else units
    if not isinstance(units, list) or len(units) != len(urls):
        raise ValueError
    parsed = []
    for value, unit in zip(quantities, units, strict=True):
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 32
            or not isinstance(unit, str)
            or unit not in {"unit", "kg"}
        ):
            raise ValueError
        try:
            d = Decimal(value)
        except InvalidOperation:
            raise ValueError from None
        if (
            not d.is_finite()
            or not 0 < d <= 1000000
            or d.as_tuple().exponent < -6
            or (unit == "unit" and d != d.to_integral_value())
        ):
            raise ValueError
        parsed.append((d, unit))
    return parsed


@dataclass(frozen=True)
class VerifiedBatchAllocations:
    """Internal explicit authorization, never inferred from mixed result shops."""

    lines: tuple[tuple[int, object, str], ...]

    @classmethod
    def from_groups(cls, results, groups):
        lines = tuple(
            (i, group["shop"]["id"], group["shop"]["tms_id"]) for group in groups for i in group["indices"]
        )
        allocation = cls(lines)
        if not allocation.matches(results):
            raise ValueError("Batch allocation does not match confirmed available lines")
        return allocation

    def matches(self, results):
        expected = {
            i: (r.get("shop", {}).get("id"), r.get("shop", {}).get("tms_id"))
            for i, r in enumerate(results)
            if r["status"] != "error"
            and (r.get("product") or {}).get("availability", {}).get("state") == "available"
        }
        actual = {i: (shop_id, tms_id) for i, shop_id, tms_id in self.lines}
        return len(actual) == len(self.lines) and actual == expected


def estimate(results, quantities, marketplace, *, allocations=None):
    currency = "UAH" if marketplace == "varus" else "RUB" if marketplace == "yandex_market" else None
    totals = {"ordinary": Decimal(0), "promotional": Decimal(0)}
    included = {key: 0 for key in totals}
    lines = []
    shops = {(r.get("shop", {}).get("id"), r.get("shop", {}).get("tms_id")) for r in results if r.get("shop")}
    authorized = (
        marketplace == "varus"
        and isinstance(allocations, VerifiedBatchAllocations)
        and allocations.matches(results)
    )
    for i, (r, (quantity, unit)) in enumerate(zip(results, quantities, strict=True)):
        p = r.get("product") or {}
        prices = p.get("prices") or {}
        state = p.get("availability", {}).get("state")
        reason = (
            "provider_error"
            if r["status"] == "error"
            else "shop_mismatch"
            if len(shops) > 1 and not authorized
            else "unavailable"
            if state == "unavailable"
            else "availability_unknown"
            if state != "available"
            else "price_basis_unknown"
            if prices.get("basis") not in {"unit", "kg"}
            else "unit_mismatch"
            if prices["basis"] != unit
            else "currency_mismatch"
            if prices.get("currency") != currency
            else None
        )
        line = {
            "index": i,
            "quantity": str(quantity),
            "unit": unit,
            "excluded_reason": reason,
            "ordinary": None,
            "promotional": None,
        }
        if reason is None:
            for kind in totals:
                try:
                    value = prices.get(kind)
                    if value is None or isinstance(value, bool) or len(str(value)) > 32:
                        continue
                    price = Decimal(str(value))
                    if (
                        not price.is_finite()
                        or not 0 <= price <= 1000000000
                        or price.as_tuple().exponent < -6
                    ):
                        continue
                    amount = price * quantity
                    line[kind] = str(amount)
                    totals[kind] += amount
                    included[kind] += 1
                except InvalidOperation:
                    continue
            if line["ordinary"] is None and line["promotional"] is None:
                line["excluded_reason"] = "missing_price"
        lines.append(line)
    result = {
        "currency": currency,
        "requested": len(results),
        "lines": lines,
        "exact_quantity_confirmed": False,
        "approximate": True,
        "warnings": [
            "No reservation, requested inventory quantity, delivery fees, or conditional discounts included",
            "Promotional subtotal includes only observed promotional prices; no ordinary-price fallback",
            "Totals rounded once to currency minor units (half up); missing coverage is not zero-priced inventory",
        ],
    }
    for kind, total in totals.items():
        result[kind] = {
            "total": str(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "included": included[kind],
            "excluded": len(results) - included[kind],
            "complete": included[kind] == len(results),
        }
    if authorized:
        result["per_shop"] = []
        identities = list(dict.fromkeys((sid, tms) for _, sid, tms in allocations.lines))
        for sid, tms in identities:
            indices = [i for i, shop_id, tms_id in allocations.lines if (shop_id, tms_id) == (sid, tms)]
            subtotal = estimate([results[i] for i in indices], [quantities[i] for i in indices], marketplace)
            for line, index in zip(subtotal["lines"], indices, strict=True):
                line["index"] = index
            result["per_shop"].append({"shop": results[indices[0]]["shop"], "indices": indices, **subtotal})
    return result
