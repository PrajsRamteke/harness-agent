"""Session cost estimation."""
from ..constants import PRICING
from .. import state


def estimated_cost() -> float:
    p = PRICING.get(state.MODEL, (3.0, 15.0))
    return (state.total_in * p[0] + state.total_out * p[1]) / 1_000_000
