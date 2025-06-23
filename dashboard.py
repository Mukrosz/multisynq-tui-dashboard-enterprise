#!/usr/bin/env python3
"""dashboard.py – Textual dashboard for StartSynq’ing Enterprise keys.
This application fetches synchronizer data from the StartSynq’ing API
and displays it in a terminal user interface (TUI) using the Textual library.
It requires an enterprise API key to access the synchronizers endpoint."""

import argparse
import asyncio
import sys
import time
from typing import Any, Dict, List

import httpx
from rich.console import RenderableType
from rich.table import Table
from textual.app import App
from textual.reactive import reactive
from textual.widgets import Static

# ──────────────────────────────────────────────────────────────────────────────
# Textual version‑agnostic imports
# ──────────────────────────────────────────────────────────────────────────────
try:  # Textual ≥ 0.41
    from textual.containers import VerticalScroll, Horizontal
except ImportError:  # older versions 
    try:
        from textual.widgets import VerticalScroll, Horizontal  # type: ignore
    except ImportError:
        from textual.widgets import ScrollView as VerticalScroll  # type: ignore
        from textual.widgets import Horizontal  # type: ignore

try:
    from textual.widgets import Header as TextualHeader  # modern versions
except ImportError:
    TextualHeader = None  # type: ignore

# ──────────────────────────────────────────────────────────────────────────────
API_URL = "https://startsynqing.com/api/synq-keys/enterprise/synchronizers"
REFRESH_EVERY = 60  # seconds
APP_TITLE = "Synchronizers Dashboard"

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
async def fetch_synchronizers(client: httpx.AsyncClient, key: str) -> tuple[List[Dict[str, Any]], str | None]:
    """Return (list_of_syncs, wallet) or ([], None) on error."""
    headers = {"X-Enterprise-API-Key": key, "Content-Type": "application/json"}
    try:
        resp = await client.get(API_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json()
        if data.get("success"):
            return data.get("synchronizers", []), data.get("owner", {}).get("walletAddress")
    except Exception as exc:  # pragma: no cover
        print(f"[fetch] {type(exc).__name__}: {exc}", file=sys.stderr)
    return [], None

# ──────────────────────────────────────────────────────────────────────────────
# Widgets
# ──────────────────────────────────────────────────────────────────────────────
class SynchronizerWidget(Static):
    """One synchronizer card – visual style via CSS (.widget-base)."""

    data: reactive[Dict[str, Any]] = reactive({})

    def __init__(self, sync: Dict[str, Any]):
        super().__init__(classes="widget-base")
        self.data = sync

    def render(self) -> RenderableType:
        table = Table.grid(padding=(0, 1))
        table.add_column(justify="right")
        table.add_column()
        for field in ("id", "key", "name", "isEnabled"):
            table.add_row(f"[bold]{field}[/]", str(self.data.get(field, "—")))
        return table


class FooterBar(Static):
    """Static footer bar with live countdown (inherits CSS colours)."""

    remaining: reactive[int] = reactive(REFRESH_EVERY)

    def __init__(self) -> None:
        super().__init__(classes="widget-base footer")
        self.styles.text_align = "center"

    def render(self) -> RenderableType:
        return f"Auto‑refresh in: {self.remaining}s  •  Press Q to quit"

# ──────────────────────────────────────────────────────────────────────────────
# Application
# ──────────────────────────────────────────────────────────────────────────────
class Dashboard(App):
    CSS_PATH = "dashboard.css"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self.client = httpx.AsyncClient(http2=True, verify=True)
        self.next_refresh_epoch = time.time() + REFRESH_EVERY
        # Will be assigned in on_mount
        self.header: Static | TextualHeader
        self.body: VerticalScroll
        self.footer: FooterBar

    # ------------------------------------------------------------------
    def _set_footer_countdown(self, remaining: int) -> None:
        """Update countdown value in the footer bar."""
        self.footer.remaining = remaining

    # ------------------------------------------------------------------
    async def on_mount(self) -> None:
        """Compose layout and start timers."""
        # Header
        if TextualHeader is not None:
            self.title = APP_TITLE
            self.header = TextualHeader(show_clock=True)
            if hasattr(self.header, "styles"):
                self.header.styles.title_align = "center"  # type: ignore[attr-defined]
        else:
            self.header = Static(APP_TITLE, classes="widget-base header")
            self.header.styles.text_align = "center"
        await self.mount(self.header)

        # Body
        self.body = VerticalScroll(id="scroll-view")
        await self.mount(self.body)

        # Footer (always static to ensure colour scheme and countdown)
        self.footer = FooterBar()
        await self.mount(self.footer)
        self._set_footer_countdown(REFRESH_EVERY)

        # Initial data & timers
        await self.refresh_widgets()
        self.set_interval(REFRESH_EVERY, self.refresh_widgets)
        self.set_interval(1.0, self._tick)

    # ------------------------------------------------------------------
    async def refresh_widgets(self) -> None:
        """Fetch data and rebuild widget grid."""
        syncs, wallet = await fetch_synchronizers(self.client, self.key)
        # Sort synchronizers by 'name' (change 'name' to another key if needed)
        syncs = sorted(syncs, key=lambda s: s.get("name", ""))
        self._update_header_wallet(wallet)
        await self._clear_body()
        await self._populate_sync_rows(syncs)
        self.next_refresh_epoch = time.time() + REFRESH_EVERY
        self._set_footer_countdown(REFRESH_EVERY)

    def _update_header_wallet(self, wallet: str | None) -> None:
        """Update wallet in header if supported."""
        if TextualHeader is not None and isinstance(self.header, TextualHeader):
            if hasattr(self.header, "sub_title"):
                self.header.sub_title = wallet or ""

    async def _clear_body(self) -> None:
        """Remove all widgets from the body."""
        for child in list(self.body.children):
            await child.remove()

    async def _populate_sync_rows(self, syncs: list[dict]) -> None:
        """Add synchronizer widgets in rows of up to 3."""
        row = None
        for idx, sync in enumerate(syncs):
            if idx % 3 == 0:
                row = Horizontal(classes="row")
                row.styles.flex_wrap = "wrap"
                row.styles.gap = (1, 1)
                await self.body.mount(row)
            widget = SynchronizerWidget(sync)
            widget.styles.width = "1fr"
            if row:
                await row.mount(widget)

    # ------------------------------------------------------------------
    async def _tick(self) -> None:
        remaining = max(0, int(self.next_refresh_epoch - time.time()))
        self._set_footer_countdown(remaining)

    async def on_unmount(self) -> None:
        await self.client.aclose()

# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Synchronizer dashboard (Textual)")
    parser.add_argument("--key", required=True, help="Enterprise API key")
    args = parser.parse_args(argv)
    Dashboard(args.key).run()

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

