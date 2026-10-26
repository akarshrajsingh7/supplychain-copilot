from backend.tools.calculation_tools import calculate_landed_cost, calculate_shortfall, calculate_tariff_exposure
from backend.tools.supply_tools import SupplyTools


def test_tariff():
    assert calculate_tariff_exposure(32000, .25) == 8000


def test_landed_cost():
    assert calculate_landed_cost(8, .5, .25) == 10.5
    assert calculate_landed_cost(8, .5) == 8.5


def test_inventory_shortfall():
    assert calculate_shortfall(4600, 400, 200) == 4000
    assert SupplyTools().get_inventory("mcu")["shortfall"] == 4000


def test_downstream():
    impact = SupplyTools().get_downstream_impact()
    assert impact["suppliers"] == ["A", "D"]
    assert impact["components"] == ["mcu", "pmic"]
    assert len(impact["products"]) == 4
    assert len(impact["orders"]) == 8
    assert "power" not in impact["products"]


def test_capacity():
    tools = SupplyTools()
    assert not tools.check_supplier_capacity("B", 4000)["feasible"]
    assert tools.check_supplier_capacity("B", 2000)["feasible"]


def test_first_plan_rejected():
    tools = SupplyTools()
    result = tools.simulate_sourcing_plan(tools.first_plan())
    assert not result.accepted
    assert any("4,000" in reason and "2,000" in reason for reason in result.violations)


def test_second_plan_accepted():
    tools = SupplyTools()
    result = tools.simulate_sourcing_plan(tools.portfolio_plan())
    assert result.accepted, result.violations
    assert result.orders_protected == 8
    assert result.savings > 0
    assert result.residual_tariff == 800
    assert result.baseline_tariff == 12300
