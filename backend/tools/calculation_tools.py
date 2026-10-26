from decimal import Decimal, ROUND_HALF_UP


def money(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def calculate_tariff_exposure(import_value: float, tariff_rate: float) -> float:
    if import_value < 0 or not 0 <= tariff_rate <= 1:
        raise ValueError("Invalid import value or tariff rate")
    return money(Decimal(str(import_value)) * Decimal(str(tariff_rate)))


def calculate_landed_cost(unit_price: float, freight: float, tariff_rate: float = 0,
                          logistics: float = 0) -> float:
    if min(unit_price, freight, logistics) < 0:
        raise ValueError("Costs must be nonnegative")
    return money(Decimal(str(unit_price)) + Decimal(str(freight)) +
                 Decimal(str(calculate_tariff_exposure(unit_price, tariff_rate))) + Decimal(str(logistics)))


def calculate_shortfall(demand: int, inventory: int, incoming: int) -> int:
    if min(demand, inventory, incoming) < 0:
        raise ValueError("Quantities must be nonnegative")
    return max(0, demand - inventory - incoming)
