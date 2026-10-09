"""Session cost estimation."""
from ..constants import is_catalog_provider, is_local_provider, model_pricing
from .. import state


def estimated_cost() -> float:
    p = model_pricing(state.MODEL, state.provider)
    if p is None:
        # Unknown price: assume Claude-like rates for built-ins (the old
        # behaviour); a models.dev provider without a listed price costs 0
        # rather than an invented number.
        p = (0.0, 0.0) if is_catalog_provider(state.provider) or is_local_provider(state.provider) else (3.0, 15.0)
    return (state.total_in * p[0] + state.total_out * p[1]) / 1_000_000
