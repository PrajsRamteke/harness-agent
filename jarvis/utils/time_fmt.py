"""Time/duration formatting helpers."""
from datetime import datetime


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _fmt_ts(ts: float) -> str:
    try: return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception: return "-"
