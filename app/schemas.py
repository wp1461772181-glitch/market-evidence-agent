from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ForecastRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)


class ForecastResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    symbol: str
    bullish_probability: float
    neutral_probability: float
    bearish_probability: float
    model_version: str
    created_at: datetime
