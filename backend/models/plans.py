from pydantic import BaseModel, Field


class Allocation(BaseModel):
    supplier: str
    component: str
    quantity: int = Field(gt=0)
    expedited: bool = False


class SourcingPlan(BaseModel):
    id: str
    title: str
    allocations: list[Allocation]


class PlanEvaluation(BaseModel):
    accepted: bool
    violations: list[str]
    baseline_cost: float
    plan_cost: float
    incremental_cost: float
    savings: float
    baseline_tariff: float
    residual_tariff: float
    exposure_reduction: float
    orders_protected: int
    order_value_protected: float
    residual_risk: list[str]
