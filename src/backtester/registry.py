"""The single merged indicator lookup: core (hand-verified) + extended (auto-verified).

Lives in its own module, not inside ``indicators.py``, to avoid a circular import:
``extended_indicators.py`` imports ``IndicatorSpec`` from ``indicators.py``, so the
merge can't live in ``indicators.py`` without ``indicators.py`` importing back from
``extended_indicators.py``. Everything downstream (``schema.py``, ``rule_strategy.py``)
should look indicators up here, not in ``indicators.CORE_INDICATORS`` directly, so both
tiers are always visible together.
"""

from .extended_indicators import EXTENDED_INDICATORS
from .indicators import CORE_INDICATORS

_collision = CORE_INDICATORS.keys() & EXTENDED_INDICATORS.keys()
if _collision:
    raise ValueError(f"extended indicator names collide with core registry: {sorted(_collision)}")

ALL_INDICATORS = {**CORE_INDICATORS, **EXTENDED_INDICATORS}
