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


class RegimeRecordOut(BaseModel):
    date: date
    adx_percentile: float | None
    trend_regime: Literal["choppy", "neutral", "trending", "insufficient_history"]
    natr_percentile: float | None
    vol_regime: Literal["low_vol", "neutral", "high_vol", "insufficient_history"]
