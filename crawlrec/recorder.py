import asyncio, json, re, signal, importlib.resources
from pathlib import Path
from urllib.parse import urlparse

from .utils import log, launch_browser, YELLOW, BOLD, RESET
from .tui import ClickUI

# ------------------- RECORDER -------------------
class Recorder:
    def __init__(self, url, output=None, slow_mo=120):
        
        self.url, self.output, self.slow_mo = url, output, slow_mo

        self.actions, self.recording = [], True
        self.browser, self.ctx = None, None

        self.shutdown_event = asyncio.Event()
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self.result_queue: asyncio.Queue = asyncio.Queue()

        self._ui_task: asyncio.Task | None = None
        self._ui_consumer_task: asyncio.Task | None = None

    def setup_signal_handlers(self):
        loop = asyncio.get_event_loop()

        def handle(sig):
            asyncio.create_task(self.safe_stop(f"{sig.name} pressed"))

        try:
            loop.add_signal_handler(signal.SIGINT, lambda: handle(signal.SIGINT))
            loop.add_signal_handler(signal.SIGTERM, lambda: handle(signal.SIGTERM))
        except NotImplementedError:
            signal.signal(signal.SIGINT, lambda *_: handle(signal.SIGINT))
            signal.signal(signal.SIGTERM, lambda *_: handle(signal.SIGTERM))

    def _make_output_path(self):
        if self.output:
            custom = Path(self.output).expanduser().resolve()
            custom.parent.mkdir(parents=True, exist_ok=True)
            return str(custom)

        base = Path.cwd() / "crawls"
        base.mkdir(parents=True, exist_ok=True)

        domain = re.sub(r"^www\.", "", urlparse(self.url).netloc)
        n = 1
        while True:
            p = base / (f"{domain}{'' if n == 1 else n}.json")
            if not p.exists():
                return str(p)
            n += 1

    async def _save(self):
        if not self.actions:
            log("No elements recorded.", color=YELLOW)
            return

        path = self._make_output_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"url": self.url, "actions": self.actions}, f, indent=2)

        print(f"{BOLD}{YELLOW}→ Saved {len(self.actions)} actions → {path}{RESET}")

    # ------------------------------------------------------------
    # Receive click data from Playwright → TUI
    # ------------------------------------------------------------
    async def on_click(self, data):
        """Playwright binding sends click → put into UI event queue."""
        if not self.recording:
            return
        await self.event_queue.put(data)

    # ------------------------------------------------------------
    # Receive selections from TUI → actions[]
    # ------------------------------------------------------------
    
    async def _consume_ui(self):
        """
        Receives:
            ("exit", selected_items_list)
        """
        while self.recording and not self.shutdown_event.is_set():
            try:
                action, items = await self.result_queue.get()
            except Exception:
                continue

            if action == "exit":
                # Save selected
                if isinstance(items, list):
                    for item in items:
                        if item not in self.actions:
                            self.actions.append(item)

                await self.safe_stop("Exit requested (UI)")
                return


    # ------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------
    async def safe_stop(self, msg="🛑 Stopping..."):
        """Stops: UI + browser + write file."""

        if not self.recording:
            return

        # tell UI to shut down (UI may ignore if already closing)
        try:
            await self.result_queue.put(("exit", None))
        except Exception:
            pass

        # shutdown Playwright context & browser
        try:
            if self.ctx:
                await asyncio.wait_for(self.ctx.close(), timeout=10)
                await asyncio.wait_for(self.browser.close(), timeout=10)
        except Exception:
            pass

        # write output file
        try:
            await self._save()
        except Exception as e:
            log(f"⚠️ Error saving: {e}", color=YELLOW)

        # wait for UI task to end cleanly
        try:
            if self._ui_task and not self._ui_task.done():
                await asyncio.wait_for(self._ui_task, timeout=2)
        except Exception:
            pass

        self.recording = False
        self.shutdown_event.set()

    # ------------------------------------------------------------
    async def record(self):
        try:
            log(f"Starting recorder for → {BOLD}{self.url}{RESET}", color=BOLD)
            await asyncio.sleep(1)
            
            self.browser, self.ctx = await launch_browser()
            self.setup_signal_handlers()

            page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()

            # Bind JS→Python
            await self.ctx.expose_binding(
                "recordClick",
                lambda src, data: asyncio.create_task(self.on_click(data)),
            )

            # Load rec.js / main onclick dom recorder
            try:
                with importlib.resources.files("crawlrec").joinpath("rec.js").open("r", encoding="utf-8") as f:
                    rec_js = f.read()
            except FileNotFoundError:
                await self.safe_stop("❌ rec.js missing")
                return

            await asyncio.wait_for(page.goto(self.url, wait_until="domcontentloaded"), timeout=30)
            await page.evaluate(rec_js)

            # Start TUI + consumer
            ui = ClickUI(self.event_queue, self.result_queue)
            self._ui_task = asyncio.create_task(ui.run_async())
            self._ui_consumer_task = asyncio.create_task(self._consume_ui())

            while self.recording and not self.shutdown_event.is_set():
                await asyncio.sleep(0.2)

            await self.safe_stop("Finished.")

        except asyncio.CancelledError:
            await self.safe_stop("Cancelled")

        except Exception as e:
            log(f"Recorder crashed: {e}", color=YELLOW)
            await self.safe_stop("Crash")