#!/usr/bin/env python3
"""
bot.service.report — Daily P&L HTML dashboard builder (DASH-01, D-15/D-16).

Generates a self-contained, no-JS, no-CDN HTML report with:
  - Summary line (trade count, wins, losses, P&L)
  - Inline SVG R-multiple histogram (7 buckets: <-3 through 3+)
  - Last-20 closed trades table
  - Open positions table

All free-text fields (ticker, exit_reason, phase) are HTML-escaped before
interpolation (T-05-04-01 — alert injection mitigation).

Reports are written to reports/YYYY-MM-DD.html + reports/latest.html (D-16).
The reports/ directory is gitignored; write_reports() creates it if absent.

Exports: ReportBuilder
"""
import html
import shutil
from pathlib import Path

from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger

# ============================================================
# Module-level logger
# ============================================================

_logger = get_logger(__name__)

# ============================================================
# R-multiple histogram buckets
# ============================================================

# Bucket boundaries (open-right intervals): [-3,-2), [-2,-1), [-1,0), [0,1), [1,2), [2,3), [3+]
# The last bucket absorbs all values >= 3.
_BINS = [-3, -2, -1, 0, 1, 2, 3]
# Labels are float-parseable strings so tests can do float(k) comparisons.
# "3" represents the overflow bucket [3, ∞).
_LABELS = ["-3", "-2", "-1", "0", "1", "2", "3"]
# Human-readable label overrides for HTML rendering (display-only)
_DISPLAY_LABELS = ["-3", "-2", "-1", "0", "1", "2", "3+"]


# ============================================================
# ReportBuilder
# ============================================================

class ReportBuilder:
    """Build daily HTML reports from StateStore data (DASH-01, D-15/D-16).

    Accepts an open StateStore for data access and an optional report_dir
    override (defaults to 'reports/' in the working directory). All HTML
    is self-contained: one inline <style> block, no <script> tags, no
    external/CDN assets — renders from file:// offline (D-15).

    store:      Open StateStore providing get_closed_trades + get_open_positions.
    report_dir: str — output directory for dated + latest.html files.
    """

    def __init__(self, store, report_dir: str = "reports") -> None:
        """Initialise the ReportBuilder.

        store:      An open StateStore instance (provides trade/position data).
        report_dir: Output directory path (created by write_reports if absent).
        """
        self._store = store
        self._report_dir = report_dir

    # ============================================================
    # Public API
    # ============================================================

    def build_daily_html(self, session_date) -> str:
        """Build a self-contained HTML report for the given session date.

        Fetches closed trades via store.get_closed_trades(session_date) and
        open positions via store.get_open_positions(). Computes summary stats,
        builds the SVG histogram and two HTML tables, and returns one complete
        HTML document (DASH-01, D-15).

        All free-text fields (code, exit_reason, phase) are HTML-escaped before
        interpolation (T-05-04-01). No <script> tag is emitted; no external/CDN
        asset URL is referenced.

        session_date: date or str — the trading session date (ET-correct).
        Returns str — complete self-contained HTML document.
        """
        session_date_str = str(session_date)

        # Fetch data from StateStore
        trades_rows = self._store.get_closed_trades(session_date_str)
        positions_rows = self._store.get_open_positions()

        return _build_html(trades_rows, positions_rows, session_date_str)

    def _build_r_histogram(self, r_multiples: list) -> dict:
        """Bucket a list of R-multiple floats into histogram bins.

        Returns a dict mapping bucket label str → count int. Buckets follow
        the [-3, -2, -1, 0, 1, 2, 3+] scheme — 7 buckets total.

        NULL values (None) are treated as 0.0 (Assumption A2).

        r_multiples: list of float|None — raw R-multiple values.
        Returns dict: {label: count} for all 7 bucket labels (zero-counts included).
        """
        return _compute_histogram(r_multiples)

    def write_reports(self, html_content: str, session_date) -> None:
        """Write dated + latest.html to self._report_dir (D-16).

        Creates the report directory if absent. Writes
        {report_dir}/{session_date}.html and copies it to
        {report_dir}/latest.html.

        Wrapped in try/except — write failures are warning-logged and never
        re-raised (mirrors append_audit defensive pattern).

        html_content: str — the full HTML document string.
        session_date: date or str — the trading session date.
        """
        _write_reports(html_content, str(session_date), self._report_dir)


# ============================================================
# Module-level helpers (also callable independently)
# ============================================================

def build_daily_html(trades_rows: list, positions_rows: list, session_date) -> str:
    """Build a self-contained HTML daily report (module-level convenience).

    trades_rows:    list of dicts from the trades table.
    positions_rows: list of dicts from the positions table.
    session_date:   date or str — the trading session date.
    Returns str — complete self-contained HTML document.
    """
    return _build_html(trades_rows, positions_rows, str(session_date))


def write_reports(html_content: str, session_date, report_dir: str = "reports") -> None:
    """Write dated + latest.html reports (module-level convenience).

    html_content: str — full HTML document.
    session_date: date or str — the trading session date.
    report_dir:   str — output directory (default 'reports/').
    """
    _write_reports(html_content, str(session_date), report_dir)


# ============================================================
# Internal helpers
# ============================================================

def _compute_histogram(r_multiples: list) -> dict:
    """Bucket R-multiple values into 7 labeled bins.

    Buckets: [-3, -2, -1, 0, 1, 2, 3+]
    Boundary convention: value v belongs to bin i if _BINS[i] <= v < _BINS[i+1].
    The overflow bucket ("3+") catches all v >= 3.
    NULL / None values are treated as 0.0 (Assumption A2).

    Returns dict: {label: count} for all 7 labels.
    """
    counts = {label: 0 for label in _LABELS}
    for r in r_multiples:
        v = float(r or 0.0)
        # Find the bucket
        placed = False
        for i in range(len(_BINS) - 1):
            if _BINS[i] <= v < _BINS[i + 1]:
                counts[_LABELS[i]] += 1
                placed = True
                break
        if not placed:
            if v < _BINS[0]:
                # v < -3 → underflow into the first ("-3") bucket
                counts[_LABELS[0]] += 1
            else:
                # v >= 3 → overflow into the last ("3", displayed "3+") bucket
                counts[_LABELS[-1]] += 1
    return counts


def _build_histogram_svg(trades_rows: list) -> str:
    """Build an inline SVG histogram of R-multiples for the report.

    Always includes the section heading "R-Multiple Histogram" so tests can
    locate the section even when there are no trades.
    When trades_rows is empty, returns the heading + "<p>No trades today.</p>".
    Otherwise returns the heading + SVG element with one <rect> per bucket.

    Negative-R buckets are colored red (#f85149); positive-R buckets green (#3fb950).
    The "0" bucket (0 <= R < 1) is green (consistent with any positive outcome).

    trades_rows: list of dicts — each may have an "r_multiple" key (NULL-safe).
    Returns str — inline HTML fragment (heading + SVG or heading + <p> message).
    """
    section_heading = "<h2>R-Multiple Histogram</h2>\n"
    if not trades_rows:
        return section_heading + "<p>No trades today.</p>"

    r_multiples = [t.get("r_multiple") for t in trades_rows]
    counts_dict = _compute_histogram(r_multiples)
    counts = [counts_dict[label] for label in _LABELS]

    max_count = max(counts) or 1
    bar_h = 100
    bar_w = 30
    gap = 8
    total_w = len(_LABELS) * (bar_w + gap)

    bars = ""
    for i, (label, count) in enumerate(zip(_DISPLAY_LABELS, counts)):
        h = int(bar_h * count / max_count)
        x = i * (bar_w + gap)
        # Negative R buckets: first 3 labels ("-3", "-2", "-1") → red; rest → green
        color = "#f85149" if i < 3 else "#3fb950"
        escaped_label = html.escape(label)
        bars += (
            f'<rect x="{x}" y="{bar_h - h}" width="{bar_w}" height="{h}" fill="{color}"/>'
            f'<text x="{x + bar_w // 2}" y="{bar_h + 12}" text-anchor="middle" '
            f'font-size="10" fill="#c9d1d9">{escaped_label}</text>'
            f'<text x="{x + bar_w // 2}" y="{bar_h - h - 3}" text-anchor="middle" '
            f'font-size="9" fill="#c9d1d9">{count}</text>'
        )

    return (
        section_heading
        + f'<svg width="{total_w + 10}" height="{bar_h + 30}" '
        + f'xmlns="http://www.w3.org/2000/svg">{bars}</svg>'
    )


def _build_html(trades_rows: list, positions_rows: list, session_date_str: str) -> str:
    """Assemble the complete HTML document.

    trades_rows:       list of dicts from trades table.
    positions_rows:    list of dicts from positions table.
    session_date_str:  str — the session date for the report title.
    Returns str — complete, self-contained HTML document.
    """
    # --- Summary aggregation ---
    n_trades = len(trades_rows)
    wins = sum(1 for t in trades_rows if (t.get("r_multiple") or 0.0) > 0)
    losses = n_trades - wins
    total_pnl = sum(
        ((t.get("exit_price") or 0.0) - (t.get("entry_price") or 0.0))
        * (t.get("quantity") or 0)
        for t in trades_rows
    )

    # --- Inline SVG histogram ---
    svg_section = _build_histogram_svg(trades_rows)

    # --- Last 20 closed trades table (trades already limited to 20 by query) ---
    rows_html = "".join(
        f"<tr>"
        f"<td>{html.escape(str(t.get('code', '')))}</td>"
        f"<td>{html.escape(str(t.get('exit_reason', '')))}</td>"
        f"<td class='num'>{(t.get('r_multiple') or 0.0):.2f}R</td>"
        f"</tr>"
        for t in trades_rows
    )

    # --- Open positions table ---
    open_html = "".join(
        f"<tr>"
        f"<td>{html.escape(str(p.get('code', '')))}</td>"
        f"<td>{html.escape(str(p.get('phase', '')))}</td>"
        f"<td class='num'>{p.get('remaining_quantity', 0)}</td>"
        f"</tr>"
        for p in positions_rows
    )

    # P&L sign prefix
    pnl_sign = "+" if total_pnl >= 0 else ""

    return (
        f"<!DOCTYPE html>\n"
        f"<html lang=\"en\">\n"
        f"<head><meta charset=\"UTF-8\">\n"
        f"<title>Trading Report {html.escape(session_date_str)}</title>\n"
        f"<style>\n"
        f"  body {{ font-family: monospace; background: #0d1117; color: #c9d1d9; margin: 2rem; }}\n"
        f"  h1 {{ color: #58a6ff; }}\n"
        f"  h2 {{ color: #58a6ff; }}\n"
        f"  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}\n"
        f"  th {{ background: #161b22; text-align: left; padding: 6px 12px; }}\n"
        f"  td {{ padding: 4px 12px; border-bottom: 1px solid #21262d; }}\n"
        f"  .num {{ text-align: right; }}\n"
        f"  .win {{ color: #3fb950; }}\n"
        f"  .loss {{ color: #f85149; }}\n"
        f"</style>\n"
        f"</head>\n"
        f"<body>\n"
        f"<h1>Daily Report &mdash; {html.escape(session_date_str)}</h1>\n"
        f"<p><b>Trades:</b> {n_trades} &nbsp; "
        f"<b>Wins:</b> {wins} &nbsp; "
        f"<b>Losses:</b> {losses} &nbsp; "
        f"<b>PnL:</b> {pnl_sign}${total_pnl:.2f}</p>\n"
        f"{svg_section}\n"
        f"<h2>Last 20 Closed Trades</h2>\n"
        f"<table><tr><th>Code</th><th>Exit Reason</th><th>R-Multiple</th></tr>\n"
        f"{rows_html}</table>\n"
        f"<h2>Open Positions</h2>\n"
        f"<table><tr><th>Code</th><th>Phase</th><th>Qty</th></tr>\n"
        f"{open_html}</table>\n"
        f"</body></html>"
    )


def _write_reports(html_content: str, session_date_str: str, report_dir: str) -> None:
    """Write dated + latest.html to report_dir (D-16).

    Creates report_dir if absent (exist_ok=True). Writes {session_date_str}.html
    and copies to latest.html via shutil.copy2. Exceptions are warning-logged and
    never re-raised (defensive pattern — mirrors append_audit).

    html_content:      str — full HTML document.
    session_date_str:  str — the session date (YYYY-MM-DD).
    report_dir:        str — output directory path.
    """
    try:
        out_dir = Path(report_dir)
        out_dir.mkdir(exist_ok=True)
        dated_path = out_dir / f"{session_date_str}.html"
        dated_path.write_text(html_content, encoding="utf-8")
        shutil.copy2(dated_path, out_dir / "latest.html")
        _logger.info(
            "report_written",
            dated=str(dated_path),
            latest=str(out_dir / "latest.html"),
        )
    except Exception:
        _logger.warning("report_write_failed", exc_info=True)
