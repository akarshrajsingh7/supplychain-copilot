from datetime import datetime
from pydantic import BaseModel, Field


class TariffEvent(BaseModel):
    id: str
    name: str
    simulated: bool = True
    country: str
    hs_code: str
    rate: float = Field(gt=0, le=1)
    reference_time: datetime
    effective_at: datetime
    description: str
