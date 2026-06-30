#!/usr/bin/env python3
"""
bot.service.alerter — Fire-and-forget Telegram alert client (TelegramAlerter).

Posts to https://api.telegram.org/bot{token}/sendMessage via stdlib urllib
wrapped in run_in_executor (D-14: zero new dependency). Delivery failures are
caught and logged but never propagate to the trade loop (ALERT-04). The alerter
gracefully no-ops when TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are unset (D-12/D-13).

Exports: TelegramAlerter
"""
import asyncio
import html
import urllib.parse
import urllib.request
import logging

from bot.safety.logger import get_logger

# ============================================================
# Module-level logger
# ============================================================

_logger = get_logger(__name__)

# ============================================================
# Exit reason labels (ALERT-02)
# ============================================================

_EXIT_REASON_LABELS = {
    "partial": "Partial Exit",
    "breakeven": "Breakeven Stop",
    "trail": "Trail Stop",
    "trail_stop": "Trail Stop",   # reconciled with manager emitted vocabulary (ALERT-02)
    "trail_up": "Trail Stop Up",
    "stop_out": "Stop Out",
    "force_close": "Force Close",
    "exit_fill": "Exit Fill",
}


# ============================================================
# TelegramAlerter
# ============================================================

class TelegramAlerter:
    """Fire-and-forget Telegram push client.

    Posts alert messages to the Telegram Bot API sendMessage endpoint via
    stdlib urllib wrapped in run_in_executor. All network failures are caught
    and logged — send() never raises. When token or chat_id are falsy, the
    alerter is disabled and performs no network calls (D-12/D-13).

    token: str — Telegram Bot API token (from TELEGRAM_BOT_TOKEN env var)
    chat_id: str — Telegram chat or channel ID (from TELEGRAM_CHAT_ID env var)
    logger: optional bound logger — uses module logger if not provided
    """

    _API_URL = "https://api.telegram.org/bot{token}/sendMessage"
    _TIMEOUT_S = 10

    def __init__(self, token: str, chat_id: str, logger=None):
        self._token = token or ""
        self._chat_id = chat_id or ""
        self._log = logger or _logger
        self._enabled = bool(self._token and self._chat_id)

    # ============================================================
    # Core transport
    # ============================================================

    def _post_blocking(self, text: str) -> None:
        """POST alert text to Telegram sendMessage endpoint (runs in executor).

        Builds the sendMessage URL from _API_URL, urlencodes payload with
        parse_mode HTML and disable_web_page_preview, then calls urlopen.
        Token is never interpolated into log lines or alert text (Pitfall 4).

        text: str — message body (HTML-escaped free-text already by caller)
        """
        url = self._API_URL.format(token=self._token)
        payload = urllib.parse.urlencode({
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=self._TIMEOUT_S) as resp:
            resp.read()  # consume response body

    async def send(self, text: str) -> None:
        """Send a message to Telegram (fire-and-forget).

        When the alerter is disabled (empty token/chat_id): logs a debug line
        and returns immediately — no network call is attempted.

        When enabled: runs _post_blocking in a thread executor via
        run_in_executor. Any exception (network error, non-2xx HTTP response,
        timeout) is caught, logged as a warning, and swallowed — this method
        never re-raises (ALERT-04).

        text: str — message body to deliver
        """
        if not self._enabled:
            self._log.debug(
                "alerter_disabled_no_op",
                preview=text[:80] if text else "",
            )
            return

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._post_blocking, text)
        except Exception:
            # Warning only — no token in this log line (Pitfall 4)
            self._log.warning(
                "telegram_send_failed",
                chat_id=self._chat_id,
                exc_info=True,
            )

    # ============================================================
    # Message builders (pure — return str, no I/O)
    # ============================================================

    def format_entry_alert(
        self,
        code: str,
        qty: int,
        entry_price: float,
        stop: float,
    ) -> str:
        """Build an entry alert message (ALERT-01).

        Includes ticker, size, entry price, and initial stop.
        Free-text field (code) is HTML-escaped (Pitfall 4 / alert injection).

        code: str — stock code e.g. "US.AAPL"
        qty: int — number of shares
        entry_price: float — fill price
        stop: float — initial stop price
        Returns str — HTML-formatted alert message
        """
        safe_code = html.escape(str(code))
        return (
            f"<b>Entry</b> {safe_code}\n"
            f"Size: {qty:,} shares\n"
            f"Entry: ${entry_price:.2f}\n"
            f"Stop: ${stop:.2f}"
        )

    def format_exit_alert(
        self,
        code: str,
        exit_reason: str,
        r_multiple: float,
    ) -> str:
        """Build an exit alert message (ALERT-02).

        Covers all exit event types: partial, breakeven, trail, stop-out,
        force-close. Unknown exit_reason values fall back to the raw escaped
        string. Free-text fields are HTML-escaped.

        code: str — stock code e.g. "US.AAPL"
        exit_reason: str — exit event type identifier
        r_multiple: float — realized R-multiple
        Returns str — HTML-formatted alert message
        """
        safe_code = html.escape(str(code))
        safe_reason = html.escape(str(exit_reason))
        label = _EXIT_REASON_LABELS.get(exit_reason, safe_reason)
        r_sign = "+" if (r_multiple or 0.0) >= 0 else ""
        return (
            f"<b>{label}</b> {safe_code}\n"
            f"R: {r_sign}{(r_multiple or 0.0):.2f}R"
        )

    def format_daily_summary(
        self,
        trades_rows: list,
        open_positions: list,
    ) -> str:
        """Build a daily summary alert message (ALERT-03).

        Computes trade count, wins, losses, realized PnL, and open risk from
        the provided rows (mirroring the `trades` and `positions` table schemas
        from bot.state.migrations).

        trades_rows: list of dicts — each dict has keys entry_price, exit_price,
            quantity, r_multiple (NULL-safe via `or 0.0`)
        open_positions: list of dicts — each dict has keys entry_price,
            initial_stop, trail_stop, remaining_quantity (matching positions
            table schema from StateStore.get_open_positions()). trail_stop
            overrides initial_stop when set; per-position risk is clamped at
            0 when a trailing stop has advanced past entry (locked profit).
        Returns str — HTML-formatted daily summary message
        """
        n_trades = len(trades_rows)
        wins = sum(1 for r in trades_rows if (r.get("r_multiple") or 0.0) > 0)
        losses = n_trades - wins

        realized_pnl = sum(
            ((r.get("exit_price") or 0.0) - (r.get("entry_price") or 0.0))
            * (r.get("quantity") or 0)
            for r in trades_rows
        )

        def _position_risk(r: dict) -> float:
            entry = r.get("entry_price") or 0.0
            trail_stop = r.get("trail_stop")
            initial_stop = r.get("initial_stop") or 0.0
            effective_stop = trail_stop if trail_stop is not None else initial_stop
            qty = r.get("remaining_quantity") or r.get("quantity") or 0
            return max(0.0, (entry - effective_stop) * qty)

        open_risk = sum(_position_risk(r) for r in open_positions)

        pnl_sign = "+" if realized_pnl >= 0 else ""
        return (
            f"<b>Daily Summary</b>\n"
            f"Trades: {n_trades} ({wins}W / {losses}L)\n"
            f"Realized PnL: {pnl_sign}${realized_pnl:.2f}\n"
            f"Open Risk: ${open_risk:.2f}"
        )

    # ============================================================
    # Convenience coroutines (build text + send)
    # ============================================================

    async def alert_entry(
        self,
        code: str,
        qty: int,
        entry_price: float,
        stop: float,
    ) -> None:
        """Build and send an entry alert (ALERT-01).

        Convenience wrapper — callers dispatch via asyncio.create_task for
        truly fire-and-forget delivery.
        """
        text = self.format_entry_alert(code, qty, entry_price, stop)
        await self.send(text)

    async def alert_exit(
        self,
        code: str,
        exit_reason: str,
        r_multiple: float,
    ) -> None:
        """Build and send an exit alert (ALERT-02).

        Convenience wrapper — callers dispatch via asyncio.create_task for
        truly fire-and-forget delivery.
        """
        text = self.format_exit_alert(code, exit_reason, r_multiple)
        await self.send(text)

    async def alert_summary(
        self,
        trades_rows: list,
        open_positions: list,
    ) -> None:
        """Build and send a daily summary alert (ALERT-03).

        Convenience wrapper — callers dispatch via asyncio.create_task for
        truly fire-and-forget delivery.
        """
        text = self.format_daily_summary(trades_rows, open_positions)
        await self.send(text)

    # ============================================================
    # Specific send methods (used by tests and external callers)
    # ============================================================

    async def send_entry_alert(
        self,
        code: str,
        qty: int,
        entry_price: float,
        initial_stop: float,
    ) -> None:
        """Send an entry alert for a new position (ALERT-01).

        code: str — stock code e.g. "US.AAPL"
        qty: int — number of shares entered
        entry_price: float — fill price
        initial_stop: float — initial stop-loss price
        """
        await self.alert_entry(code, qty, entry_price, initial_stop)

    async def send_exit_alert(
        self,
        code: str,
        exit_reason: str,
        r_multiple: float,
    ) -> None:
        """Send an exit event alert (ALERT-02).

        Handles all exit reason types: stop_out, partial, breakeven,
        trail_up, force_close, exit_fill.

        code: str — stock code e.g. "US.AAPL"
        exit_reason: str — exit event type
        r_multiple: float — realized R-multiple for this exit
        """
        await self.alert_exit(code, exit_reason, r_multiple)

    async def send_daily_summary(
        self,
        trades: int,
        wins: int,
        pnl_usd: float,
        open_risk_usd: float,
    ) -> None:
        """Send a daily summary alert (ALERT-03).

        Accepts pre-computed aggregates (trades, wins, pnl, open_risk)
        instead of raw rows — convenient for callers that have already
        aggregated from the StateStore.

        trades: int — total closed trades today
        wins: int — number of winning trades (r_multiple > 0)
        pnl_usd: float — total realized PnL in USD
        open_risk_usd: float — sum of open position risk in USD
        """
        losses = trades - wins
        pnl_sign = "+" if pnl_usd >= 0 else ""
        text = (
            f"<b>Daily Summary</b>\n"
            f"Trades: {trades} ({wins}W / {losses}L)\n"
            f"Realized PnL: {pnl_sign}${pnl_usd:.2f}\n"
            f"Open Risk: ${open_risk_usd:.2f}"
        )
        await self.send(text)
