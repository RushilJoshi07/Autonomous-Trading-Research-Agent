from datetime import date
from typing import Literal

from pydantic import BaseModel


class PriceBarOut(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class IndicatorValueOut(BaseModel):
    date: date
    value: float


class IndicatorInfo(BaseModel):
    name: str
    tier: Literal["core", "extended"]
    verified: bool
    inputs: list[str]
    params: dict[str, tuple[float, float]]
