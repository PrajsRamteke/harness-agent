"""What a request actually sends for thinking — and what to do when it's refused.

``state.think_mode`` / ``state.think_effort`` are the *preference* (what the user
picked, kept across model switches). :func:`effective` turns that preference into
what the current model can take, using :mod:`jarvis.auth.thinking_caps`:
an unsupported level becomes the nearest supported one, a model that can't be
switched off stays on at its lowest level, a model that doesn't think is sent
nothing. :func:`apply` writes that onto the request kwargs for every provider
(one place, shared by the main turn and subagents), and :func:`recover` handles
a provider that refuses the thinking settings anyway: it remembers the refusal
and says what to resend, so the turn goes on instead of failing.

Nothing here raises into the request path — any failure falls back to the old,
unclamped behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..auth import thinking_caps as caps_mod
from ..auth.thinking_caps import (
    MODE_FIXED, MODE_NONE, MODE_TOGGLE, REFUSED_ALL, ThinkCaps,
    looks_like_thinking_refusal, mark_refused, nearest_level, thinking_caps,
)
from ..constants import DEFAULT_THINK_EFFORT, THINKING_BUDGET_TOKENS
from ..constants.providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENCODE,
    PROVIDER_OPENCODE_ZEN,
    claude_thinking_kwargs,
    claude_uses_adaptive_thinking,
    is_catalog_provider,
    is_local_provider,
)

# Providers whose client reads ``thinking["effort"]`` (the OpenAI-style wires).
_EFFORT_IN_THINKING = (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN, PROVIDER_HARNESS_AGENT, PROVIDER_OPENAI_CODEX,
                       PROVIDER_ANTIGRAVITY)


@dataclass(frozen=True)
class Effective:
    """The thinking setting a request will really use."""

    on: bool                  # send thinking at all
    effort: str = ""          # the level sent; "" = no level applies
    note: str = ""            # why this differs from the preference ("" = it doesn't)
    caps: ThinkCaps = caps_mod.UNKNOWN
    asked: str = ""           # the preferred level ("none" when thinking was off)

    @property
    def adjusted(self) -> bool:
        return bool(self.note)

    @property
    def label(self) -> str:
        """Short text for the footer / sidebar: ``high``, ``on``, ``off``."""
        if not self.on:
            return "off"
        return self.effort or "on"


def effective(model: str, provider: str | None, think_mode: bool, effort: str) -> Effective:
    """The setting ``model`` takes for the preference (``think_mode``, ``effort``)."""
    want = bool(think_mode) and effort != "none"
    asked = effort if want else "none"
    try:
        caps = thinking_caps(model, provider)
    except Exception:
        caps = caps_mod.UNKNOWN
    mode = caps.mode
    if mode == caps_mod.MODE_UNKNOWN:
        return Effective(want, effort if want else "", "", caps, asked)
    short = model.split("/")[-1] if model else "this model"
    if mode == MODE_NONE:
        note = f"{short} doesn't support thinking — sending without it" if want else ""
        return Effective(False, "", note, caps, asked)
    if mode == MODE_FIXED:
        note = "" if want else f"{short} always thinks and can't be switched off"
        return Effective(True, "", note, caps, asked)
    if mode == MODE_TOGGLE:
        return Effective(want, "", "", caps, asked)
    if not want:
        if caps.can_off:
            return Effective(False, "", "", caps, asked)
        lvl = caps.lowest
        return Effective(True, lvl, f"{short} can't turn thinking off — using its lowest level ({lvl})", caps, asked)
    lvl = nearest_level(effort, caps.levels)
    note = "" if lvl == effort else f"{short} has no `{effort}` thinking level — using `{lvl}`"
    return Effective(True, lvl, note, caps, asked)


def effective_now() -> Effective:
    """:func:`effective` for the live session (what the UIs show)."""
    from .. import state

    return effective(state.MODEL, state.provider, state.think_mode, state.think_effort)


def _strip(kwargs: dict[str, Any]) -> None:
    """Remove whatever thinking settings an earlier :func:`apply` added."""
    kwargs.pop("thinking", None)
    oc = kwargs.get("output_config")
    if isinstance(oc, dict):
        oc.pop("effort", None)
        if not oc:
            kwargs.pop("output_config", None)


def apply(
    kwargs: dict[str, Any], *, provider: str, model: str, client: Any,
    think_mode: bool, effort: str,
) -> Effective:
    """Add the thinking settings for ``model`` to the request ``kwargs``."""
    try:
        eff = effective(model, provider, think_mode, effort)
    except Exception:
        eff = Effective(bool(think_mode) and effort != "none", effort, "", caps_mod.UNKNOWN, effort)
    caps = eff.caps
    if provider == PROVIDER_ANTHROPIC and claude_uses_adaptive_thinking(model):
        # Claude 5: adaptive thinking + effort; budget_tokens is a 400 there.
        kwargs.update(claude_thinking_kwargs(eff.on, eff.effort or DEFAULT_THINK_EFFORT))
        return eff
    from anthropic import Anthropic

    openai_style = provider in _EFFORT_IN_THINKING or is_local_provider(provider) or (
        is_catalog_provider(provider) and not isinstance(client, Anthropic)
    )
    known = caps.known
    if eff.on:
        if known and caps.mode == MODE_FIXED:
            return eff  # always thinks by itself: nothing to ask for
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS}
        if openai_style:
            if known:
                # The level was checked against this model: send it as is (an
                # empty one — an on/off model — sends no level at all).
                kwargs["thinking"]["effort"] = eff.effort
                kwargs["thinking"]["effort_exact"] = True
            else:
                kwargs["thinking"]["effort"] = eff.effort or effort
    elif openai_style and not (known and caps.mode == MODE_NONE):
        # OpenCode (DeepSeek, etc.) needs an explicit "disabled" to switch thinking
        # off — but a model known not to think is sent nothing at all.
        kwargs["thinking"] = {"type": "disabled"}
    return eff


_announced: set[tuple] = set()


def take_note(eff: Effective, provider: str, model: str) -> str:
    """The notice to print for an adjusted setting — once per (model, preference),
    not on every request of a long turn."""
    if not eff.note:
        return ""
    key = (provider, model, eff.asked, eff.effort, eff.on)
    if key in _announced:
        return ""
    _announced.add(key)
    return eff.note


def recover(
    err: BaseException, kwargs: dict[str, Any], *, provider: str,
    model: str, client: Any, think_mode: bool, effort: str,
) -> str:
    """The provider answered 400 about the thinking settings we sent.

    Remembers the refusal, rebuilds the thinking part of ``kwargs`` from what is
    now known (one level down, or none), and returns the notice to print.
    Returns "" when the error isn't about thinking, or nothing was sent — the
    caller then treats it like any other 400.
    """
    kind = looks_like_thinking_refusal(err)
    if not kind:
        return ""
    try:
        # What was sent = what the setting resolved to *before* this refusal.
        eff = effective(model, provider, think_mode, effort)
        if not (eff.on or eff.effort or "thinking" in kwargs):
            return ""  # nothing about thinking went out, so it can't be the cause
        if kind == "effort" and eff.effort:
            mark_refused(provider, model, eff.effort)
            what = f"`{eff.effort}` thinking level"
        else:
            mark_refused(provider, model, REFUSED_ALL)
            what = "thinking settings"
        _strip(kwargs)
        new = apply(kwargs, provider=provider, model=model, client=client,
                    think_mode=think_mode, effort=effort)
        if new.on == eff.on and new.effort == eff.effort:
            # The catalog had nothing narrower to offer: send without thinking.
            _strip(kwargs)
            if provider == PROVIDER_ANTHROPIC and claude_uses_adaptive_thinking(model):
                kwargs.update(claude_thinking_kwargs(False, "low"))
            after = "without thinking"
        else:
            after = f"with {new.label} thinking" if new.on else "without thinking"
        short = model.split("/")[-1] or model
        return f"{short} refused the {what} — retrying {after}"
    except Exception:
        return ""


# ── what the UIs show ─────────────────────────────────────────────────────────

def public(model: str | None = None, provider: str | None = None,
           think_mode: bool | None = None, effort: str | None = None) -> dict[str, Any]:
    """JSON-safe description for the web UI (and tests): the model's capabilities,
    the setting it will really use, and the rows to show in the picker."""
    from .. import state

    model = state.MODEL if model is None else model
    provider = state.provider if provider is None else provider
    think_mode = state.think_mode if think_mode is None else think_mode
    effort = state.think_effort if effort is None else effort
    eff = effective(model, provider, think_mode, effort)
    caps = eff.caps
    current = eff.effort if eff.on and eff.effort else ("on" if eff.on else "none")
    rows = caps_mod.choice_rows(caps, current)
    return {
        "known": caps.known,
        "mode": caps.mode,
        "source": caps.source,
        "levels": list(caps.levels),
        "can_off": caps.can_off,
        "default": caps.default,
        "learned": caps.learned,
        "summary": caps.summary(),
        "on": eff.on,
        "effort": eff.effort,
        "label": eff.label,
        "note": eff.note,
        "current": current,
        "choices": rows,
    }


def check_choice(value: str, model: str | None = None, provider: str | None = None) -> str:
    """'' when ``value`` ("none" / a level) can be set for the model, else the
    reason — for ``/think`` and the web API to refuse instead of silently clamp."""
    from .. import state

    model = state.MODEL if model is None else model
    provider = state.provider if provider is None else provider
    caps = thinking_caps(model, provider)
    short = model.split("/")[-1] or model
    if caps.takes(value):
        return ""
    if value == "none":
        return f"{short} always thinks and can't be switched off"
    mode = caps.mode
    if mode == MODE_NONE:
        return f"{short} doesn't support thinking"
    if mode == MODE_FIXED:
        return f"{short} always thinks — there is no level to choose"
    if mode == MODE_TOGGLE:
        return f"{short} only has thinking on or off — no `{value}` level"
    have = ", ".join(reversed(caps.levels)) or "none"
    return f"{short} doesn't support `{value}` thinking — it takes: {have}"


def set_preference(value: str, *, save: bool = True) -> str:
    """Apply a ``/think``-style choice to the session: ``on`` / ``off`` / ``none`` /
    a level. Returns "" on success, else why it can't be set (nothing changed).

    The one entry point for ``/think``, the TUI picker and the web API, so all
    three refuse the same things for the same reasons.
    """
    from .. import state
    from ..constants import THINK_EFFORTS

    value = (value or "").strip().lower()
    if value in ("on", "true", "yes"):
        caps = thinking_caps(state.MODEL, state.provider)
        if caps.mode == MODE_NONE:
            return check_choice("high") or f"{state.MODEL} doesn't support thinking"
        state.think_mode = True
        if state.think_effort == "none":
            state.think_effort = DEFAULT_THINK_EFFORT
    elif value in ("off", "false", "no", "none"):
        reason = check_choice("none")
        if reason:
            return reason
        state.think_mode = False
        if value == "none":
            state.think_effort = "none"
    elif value in THINK_EFFORTS:
        reason = check_choice(value)
        if reason:
            return reason
        state.think_mode = True
        state.think_effort = value
    else:
        return f"unknown thinking setting `{value}`"
    if save:
        state.save_think_config()
    return ""


def describe_levels(model: str | None = None, provider: str | None = None) -> str:
    """Plain text: what ``/think`` accepts for the current model."""
    from .. import state

    model = state.MODEL if model is None else model
    caps = thinking_caps(model, provider)
    short = model.split("/")[-1] or model
    extra = " (narrowed after the provider refused a level)" if caps.learned else ""
    src = f" · from {caps.source}" if caps.source else ""
    return f"{short}: {caps.summary()}{extra}{src}"
