"""Session cost estimation."""
from ..constants import is_catalog_provider, is_local_provider, model_pricing
from .. import state


def estimated_cost() -> float:
    """What this chat has cost: every request added up (the usage ledger) —
    else, for a chat from before it, the latest prompt and output priced."""
    try:
        from ..storage import usage_log

        chat = usage_log.chat_totals(state.current_session_id)
    except Exception:
        chat = None
    if chat:
        return float(chat["cost"])
    p = model_pricing(state.MODEL, state.provider)
    if p is None:
        # Unknown price: assume Claude-like rates for built-ins (the old
        # behaviour); a models.dev provider without a listed price costs 0
        # rather than an invented number.
        p = (0.0, 0.0) if is_catalog_provider(state.provider) or is_local_provider(state.provider) else (3.0, 15.0)
    return (state.total_in * p[0] + state.total_out * p[1]) / 1_000_000
