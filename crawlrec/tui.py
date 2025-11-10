from __future__ import annotations

from typing import List, Dict, Any, Set, Tuple

from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.containers import VerticalScroll
from textual import events


def _sig(item: Dict[str, Any]) -> Tuple:
    """Signature to prevent duplicates."""
    return (
        item.get("text"),
        item.get("href"),
        item.get("selector"),
        item.get("xpathSelector"),
    )


class ClickUI(App):
    """TUI for showing clicks + selecting them."""

    CSS = """
    Screen {
        layout: vertical;
        background: #0c0c0c;
        color: #ff4d4d;
    }

    #banner {
        background: #250000;  /* <- solid dark-red (no gradient) */
        padding: 1;
        text-style: bold;
        color: #ff1a1a;
        text-align: center;
    }

    #help {
        background: #1a0000;
        padding: 1;
        text-style: bold;
        color: #ff4d4d;
    }

    .item {
        padding: 1;
        color: #ff6666;
    }

    .item-selected {
        padding: 1;
        background: #330000;
        text-style: bold;
        color: #ff9999;
    }

    .cursor {
        text-style: bold reverse;
    }
    """

    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("s", "save_quit_app", "Save+Quit"),
        ("d", "discard_selected", "Discard"),
        ("enter", "toggle", "Toggle"),
    ]

    def __init__(self, event_queue, result_queue):
        super().__init__()
        self.event_queue = event_queue
        self.result_queue = result_queue

        self.items: List[Dict[str, Any]] = []
        self.items_sig: Set[Tuple] = set()
        self.selected: Set[int] = set()
        self.cursor: int = 0
        self.list_container: VerticalScroll | None = None

    # ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("CrawlRec · stexz01", id="banner")

        self.list_container = VerticalScroll()
        yield self.list_container

        yield Static(
            "↑/↓ move  ·  ENTER select  ·  S save+quit  ·  Q quit",
            id="help"
        )

    # ------------------------------------------------------------
    async def _event_worker(self):
        """Receives clicks from recorder."""
        while True:
            
            data = await self.event_queue.get()
            
            sig = _sig(data)
            
            
            try:
                if sig not in self.items_sig:
                    self.items_sig.add(sig)
                    self.items.insert(0, data)

                    self.selected = {i + 1 for i in self.selected}
                    self.refresh_list()
                    
            except TypeError:
                # signature contains an unhashable object -> skip item silently -> for svg logos href/text selector
                continue

    async def on_mount(self):
        self.run_worker(self._event_worker, exclusive=False)
        self.refresh_list()

    # ------------------------------------------------------------
    def refresh_list(self):
        if not self.list_container:
            return

        for child in list(self.list_container.children):
            child.remove()

        for idx, item in enumerate(self.items):
            text = item.get("text")
            href = item.get("href")

            if text and href and text != href:
                display = f"{href}"
            else:
                display = text or href or "[no text]"

            classes = ["item"]
            if idx in self.selected:
                classes.append("item-selected")

            arrow = "➤ " if idx == self.cursor else "  "
            label = f"{arrow}{display}"

            self.list_container.mount(Static(label, classes=" ".join(classes)))

        self.refresh()

    # ------------------------------------------------------------
    async def on_key(self, event: events.Key):
        key = event.key

        if key == "up":
            if self.cursor > 0:
                self.cursor -= 1
                self.refresh_list()

        elif key == "down":
            if self.cursor < len(self.items) - 1:
                self.cursor += 1
                self.refresh_list()

    # ------------------------------------------------------------
    def action_toggle(self):
        if self.cursor >= len(self.items):
            return
        if self.cursor in self.selected:
            self.selected.remove(self.cursor)
        else:
            self.selected.add(self.cursor)
        self.refresh_list()

    # ------------------------------------------------------------
    async def action_save_quit_app(self):
        chosen = [
            self.items[i]
            for i in sorted(self.selected)
            if i < len(self.items)
        ]
        chosen = chosen[::-1] # accending order actions
        await self.result_queue.put(("exit", chosen))
        self.exit()
    
    async def action_quit_app(self):
        await self.result_queue.put(("exit", []))
        self.exit()