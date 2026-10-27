from pydantic import BaseModel, ConfigDict, Field


class RunPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    simulate_tool_failure: bool = False
    max_incremental_cost_eur: float = Field(default=6000, ge=0, le=1000000)
    max_tool_attempts: int = Field(default=40, ge=1, le=100)
