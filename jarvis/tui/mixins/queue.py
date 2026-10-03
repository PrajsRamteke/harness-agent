"""Messages queued while Jarvis works (mixed into ``JarvisTUI``).

The bar above the composer (``queue_bar.QueueBar``) lists them with
``⚡ send now`` · ``✎ edit`` · ``✕``; the web page shows the same list
(``queue`` event, ``/api/queue``). The queue itself is ``jarvis/prompt_queue.py``.

* **send now** marks a message ``steer``: the running turn's worker takes it at
  its next step — between tool calls — and the model reads it right away
  (``_inject_steered``). If the reply ends first it simply runs next.
* **edit** moves the text into the message box; the row stays in its place,
  marked "editing" (``held``), so ↵ puts it back exactly there and esc leaves
  it as it was. A draft you were typing comes back afterwards.
"""
from __future__ import annotations

from rich.markup import escape as _rich_escape

from ... import prompt_queue, state
from ..queue_bar import QueueBar


class QueueMixin:
    _queue_editing: str = ""          # id of the queued message in the composer
    _queue_edit_draft: tuple | None = None  # (text, registry) typed before the edit

    # ─── the bar ─────────────────────────────────────────────────────────
    @staticmethod
    def _stash_preview(msg, max_len: int = 56) -> str:
        preview = prompt_queue.label(msg).replace("\n", " ").strip()
        if len(preview) > max_len:
            preview = preview[: max_len - 1] + "…"
        return _rich_escape(preview)

    def _refresh_queue_bar(self) -> None:
        try:
            self.query_one("#queuebar", QueueBar).show_items(prompt_queue.items())
        except Exception:
            pass
        self._sync_web_queue()

    def _stash_prompt(self, text: str, *, files: list[str] | None = None) -> None:
        """Queue a prompt while the agent is busy (FIFO; shown in the queue bar).

        ``files``: upload ids from the web remote (``jarvis/media.py``).
        """
        from ...prompt_attachments import snapshot_registry

        prompt_queue.add(text, snapshot_registry(), files or None)
        self._refresh_queue_bar()

    # ─── clicks (queue bar rows) and web actions ─────────────────────────
    def queue_action(self, action: str, qid: str = "") -> None:
        if action == "clear":
            n = prompt_queue.clear()
            self._set_status(f"cleared {n} queued message{'s' if n != 1 else ''}")
        elif action == "send":
            item = prompt_queue.steer(qid, True)
            if item is not None and item.steer:
                self._set_status("⚡ sent — Jarvis reads it at its next step")
        elif action == "unsteer":
            if prompt_queue.steer(qid, False) is not None:
                self._set_status("back in the queue — runs when Jarvis finishes")
        elif action == "edit":
            self._edit_queued(qid)
        elif action == "remove":
            if qid == self._queue_editing:
                self._queue_edit_cancel(restore=False)
            if prompt_queue.remove(qid) is not None:
                self._set_status("removed from the queue")
        self._refresh_queue_bar()

    def _handle_web_queue(self, data: dict) -> dict:
        """``/api/queue`` on the main thread: same queue, then both views refresh."""
        result = prompt_queue.apply_op(data)
        if result.get("ok"):
            op = str(data.get("op") or "")
            if op == "steer":
                self._set_status("⚡ web · sent — Jarvis reads it at its next step")
            elif op == "remove" and data.get("id") == self._queue_editing:
                self._queue_edit_cancel(restore=False)
        self._refresh_queue_bar()
        return result

    # ─── edit in the message box ─────────────────────────────────────────
    def _composer(self):
        from ..prompt_area import PromptArea

        return self.query_one("#prompt", PromptArea)

    def _put_in_composer(self, text: str) -> None:
        prompt = self._composer()
        self._popup_suppressed_for = text
        prompt.text = text
        lines = text.split("\n")
        prompt.move_cursor((len(lines) - 1, len(lines[-1])))
        prompt.focus()

    def _edit_queued(self, qid: str) -> bool:
        item = prompt_queue.find(qid)
        if item is None:
            return False
        if item.files:
            # Its files live on the web side; the terminal can't re-attach them.
            self._set_status("this message has web attachments — edit it on the web page")
            return False
        from ...prompt_attachments import restore_registry, snapshot_registry

        prompt = self._composer()
        if self._queue_editing and self._queue_editing != qid:
            self._queue_edit_save(prompt.text)  # switching rows keeps the first edit
        elif not self._queue_editing and (prompt.text or "").strip():
            self._queue_edit_draft = (prompt.text, snapshot_registry())
        self._queue_editing = qid
        prompt_queue.set_held(qid, True)
        reg = item[1] if isinstance(item[1], tuple) else None
        if reg:
            restore_registry(*reg)
        self._put_in_composer(item.text)
        self._refresh_queue_bar()
        self._set_status("editing a queued message — ↵ saves it in place · esc cancels")
        return True

    def _pop_queued_for_edit(self) -> bool:
        """↑ in an empty box while Jarvis works: edit the newest queued message."""
        if self._queue_editing:
            return False
        waiting = [it for it in prompt_queue.items() if not it.held]
        if not waiting:
            return False
        if waiting[-1].files:
            self._set_status("the last queued message has web attachments — it stays queued")
            return False
        return self._edit_queued(waiting[-1].id)

    def _restore_draft(self) -> None:
        from ...prompt_attachments import reset_registry, restore_registry

        draft, self._queue_edit_draft = self._queue_edit_draft, None
        reset_registry()
        try:
            prompt = self._composer()
        except Exception:
            return
        if draft:
            text, reg = draft
            if reg:
                restore_registry(*reg)
            self._put_in_composer(text)
        else:
            prompt.clear()
        self._last_input_value = ""

    def _queue_edit_save(self, text: str) -> bool:
        """Put the edited text back in its row. False: the row is gone (it ran or was removed)."""
        from ...prompt_attachments import snapshot_registry

        qid, self._queue_editing = self._queue_editing, ""
        if not qid:
            return False
        saved = prompt_queue.edit(qid, text, registry=snapshot_registry())
        return saved is not None

    def _queue_edit_submit(self, text: str) -> bool:
        """↵ while editing a queued message. True when handled (kept queued)."""
        qid = self._queue_editing
        if not qid:
            return False
        still_queued = prompt_queue.find(qid) is not None
        if self._busy and still_queued:
            self._queue_edit_save(text)
            self._restore_draft()
            self._refresh_queue_bar()
            self._set_status("queued message updated" if text.strip() else "removed from the queue")
            return True
        # Jarvis is free (or the row was dropped elsewhere): it sends like a new message.
        self._queue_editing = ""
        if still_queued:
            prompt_queue.remove(qid)
        self._refresh_queue_bar()
        return False

    def _queue_edit_cancel(self, *, restore: bool = True) -> bool:
        """Esc while editing: the queued message stays as it was."""
        qid = self._queue_editing
        if not qid:
            return False
        self._queue_editing = ""
        prompt_queue.set_held(qid, False)
        if restore:
            self._restore_draft()
            self._set_status("edit cancelled — the message stays queued")
        else:
            self._queue_edit_draft = None
        self._refresh_queue_bar()
        return True

    def _queue_edit_abandon(self) -> None:
        """The chat changed under an edit: drop the row, leave the text in the box."""
        qid, self._queue_editing = self._queue_editing, ""
        self._queue_edit_draft = None
        if qid:
            prompt_queue.remove(qid)

    # ─── "send now": the running turn takes it between two steps ────────
    def _inject_steered(self) -> bool:
        """Worker thread, between tool calls: add "send now" messages to the turn.

        They join the tool-result message the model reads next (the API needs
        user/assistant turns to alternate), each as text starting with
        ``prompt_queue.STEER_HEAD``. Returns True when something was added.
        """
        if not prompt_queue.has_steered():
            return False
        if not state.messages or state.messages[-1].get("role") != "user":
            return False
        items = prompt_queue.take_steered()
        if not items:
            return False
        from ...prompt_attachments import AttachmentRegistry, expand_attachment_tokens
        from ...prompt_refs import expand_file_refs

        blocks: list[dict] = []
        shown: list[tuple[str, list[str]]] = []
        for item in items:
            text = item.text
            reg = item[1] if isinstance(item[1], tuple) else None
            if reg:
                registry = AttachmentRegistry()
                registry.restore(reg[0])
                if reg[1]:
                    registry.llm_paths = dict(reg[1])
                text, _ = expand_attachment_tokens(text, registry)
            text, _ = expand_file_refs(text)
            blocks.extend(prompt_queue.steer_blocks(item, text))
            shown.append((item.text, item.files))
        if not prompt_queue.attach_to_last(blocks):
            return False
        try:
            self.call_from_thread(self._show_steered, shown)
        except Exception:
            pass
        return True

    def _show_steered(self, shown: list[tuple[str, list[str]]]) -> None:
        """UI thread: the sent messages appear in the transcript, in order."""
        from ... import media
        from ..transcript import UserBlock

        transcript = self._transcript()
        for text, ids in shown:
            files = [m for m in (media.get(i) for i in ids) if m]
            display = text
            if files:
                line = media.summary_line(files, limit=4)
                display = f"{text}\n{line}" if text.strip() else line
            transcript.add(UserBlock(display, flash=True, steer=True))
            if self._web_bridge is not None:
                event = {"role": "you", "text": text, "title": "you", "steered": True}
                if files:
                    event["attachments"] = [media.public(m) for m in files]
                self._web_bridge.emit("message", event)
        transcript.follow()
        self._refresh_queue_bar()
