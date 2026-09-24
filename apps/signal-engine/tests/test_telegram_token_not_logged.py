"""The Telegram bot token must never reach the logs.

The Bot API carries the token in the URL path, and httpx logs request URLs at
INFO. This sends a real request through httpx (to a mock transport) the way
`telegram._send` does, with the root logger at INFO as every entrypoint sets
it, and asserts the token appears nowhere in what was logged.
"""

from __future__ import annotations

import logging

import httpx

from src.news_trader import telegram

FAKE_TOKEN = "123456:TEST-token-that-must-not-leak"


def test_httpx_request_lines_do_not_carry_the_token(caplog):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    with caplog.at_level(logging.INFO):           # the root level every entrypoint uses
        with httpx.Client(transport=transport) as client:
            client.post(telegram._API_BASE.format(token=FAKE_TOKEN), json={"text": "x"})
    assert FAKE_TOKEN not in caplog.text


def test_httpx_logger_is_held_at_warning_by_the_telegram_module():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
