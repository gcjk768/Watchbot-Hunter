"""Alerts go to the private admin chat, at most one per kind per run, never to the channel."""
from __future__ import annotations

import logging

log = logging.getLogger("watchbot.alerts")


class Alerts:
    def __init__(self, tg, admin_chat_id, item_chat_id, vault=None):
        if admin_chat_id and str(admin_chat_id) == str(item_chat_id):
            raise ValueError("telegram.admin_chat_id must not be the channel")
        self.tg, self.admin, self.vault, self.sent = tg, admin_chat_id, vault, set()

    def send(self, kind: str, text: str) -> None:
        if kind in self.sent:
            return
        self.sent.add(kind)
        log.warning("ALERT %s: %s", kind, text)
        if self.vault:
            try:
                self.vault.alert(kind, text)
            except Exception as ex:
                log.error("alert not written to the vault: %s", ex)
        if self.admin and self.tg:
            try:
                self.tg.call("sendMessage", chat_id=self.admin, text=f"watchbot alert: {kind}\n\n{text}",
                             link_preview_options={"is_disabled": True})
            except Exception as ex:   # an alert must never break the run
                log.error("alert not delivered: %s", ex)
