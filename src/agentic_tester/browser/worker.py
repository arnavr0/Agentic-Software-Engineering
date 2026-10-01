"""Async Playwright worker with isolated mission contexts and evidence buffers."""

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

from agentic_tester.agent.action_space import ActionDecision, ActionType
from agentic_tester.config import Settings
from agentic_tester.models.element import ElementRef
from agentic_tester.models.observation import ConsoleEntry, NetworkEntry


class BrowserWorker:
    """Controls one Playwright browser process and one mission context at a time."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._settings: Settings | None = None
        self._console_buffer: list[ConsoleEntry] = []
        self._network_buffer: list[NetworkEntry] = []
        self._request_started: dict[int, float] = {}

    @property
    def page(self) -> Page:
        """Return the active page or fail with a useful lifecycle error."""

        if self._page is None:
            raise RuntimeError("No active mission context; call create_context first")
        return self._page

    @property
    def context(self) -> BrowserContext:
        """Return the active mission context."""

        if self._context is None:
            raise RuntimeError("No active mission context; call create_context first")
        return self._context

    @property
    def is_context_active(self) -> bool:
        """Whether a browser context and page are currently available."""

        return self._context is not None and self._page is not None

    @property
    def is_browser_healthy(self) -> bool:
        """Whether the session-level browser process is connected."""

        if self._browser is None:
            return False
        is_connected = getattr(self._browser, "is_connected", None)
        return bool(is_connected() if callable(is_connected) else True)

    async def start_browser(self, settings: Settings) -> None:
        """Launch the session-level browser process once."""

        if self._browser is not None:
            return
        self._settings = settings
        self._playwright = await async_playwright().start()
        browser_launcher = getattr(self._playwright, settings.browser_type)
        self._browser = await browser_launcher.launch(
            headless=settings.headless,
            slow_mo=settings.slow_mo_ms,
        )

    async def stop_browser(self) -> None:
        """Close the active context and browser process safely."""

        try:
            if self._context is not None:
                await self._close_context_without_trace()
        finally:
            try:
                if self._browser is not None:
                    await self._browser.close()
            finally:
                try:
                    if self._playwright is not None:
                        await self._playwright.stop()
                finally:
                    self._browser = None
                    self._playwright = None
                    self._settings = None

    async def create_context(self, settings: Settings | None = None) -> None:
        """Create a fresh context and page for one isolated mission."""

        if self._context is not None:
            await self._close_context_without_trace()
        active_settings = settings or self._settings
        if active_settings is None:
            raise RuntimeError("Settings are required before creating a browser context")
        if self._browser is None:
            await self.start_browser(active_settings)
        assert self._browser is not None
        self._settings = active_settings
        self._context = await self._browser.new_context(
            viewport={
                "width": active_settings.viewport_width,
                "height": active_settings.viewport_height,
            }
        )
        await self._context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(active_settings.default_timeout_ms)
        self._page.set_default_navigation_timeout(active_settings.default_timeout_ms)
        self._console_buffer.clear()
        self._network_buffer.clear()
        self._request_started.clear()
        self._page.on("console", self._on_console)
        self._page.on("request", self._on_request)
        self._page.on("response", self._on_response)
        self._page.on("requestfailed", self._on_request_failed)

    async def close_context(self, trace_output_path: Path) -> None:
        """Stop mission tracing, persist the trace, and dispose the context."""

        if self._context is None:
            return
        trace_output_path.parent.mkdir(parents=True, exist_ok=True)
        context = self._context
        try:
            await context.tracing.stop(path=str(trace_output_path))
        finally:
            try:
                await context.close()
            finally:
                self._context = None
                self._page = None
                self._console_buffer.clear()
                self._network_buffer.clear()
                self._request_started.clear()

    async def navigate(self, url: str) -> None:
        """Navigate the active page and wait for the DOM to be ready."""

        await self.page.goto(url, wait_until="domcontentloaded")

    async def click(self, element: ElementRef) -> None:
        """Resolve and click an agent-owned element reference."""

        await self._locator(element).click()

    async def fill(self, element: ElementRef, value: str) -> None:
        """Resolve and fill an agent-owned input reference."""

        await self._locator(element).fill(value)

    async def select_option(self, element: ElementRef, value: str) -> None:
        """Resolve and select an option on a native select control."""

        await self._locator(element).select_option(value)

    async def press_key(self, key: str) -> None:
        """Press a keyboard key on the active page."""

        await self.page.keyboard.press(key)

    async def scroll(self, direction: Literal["up", "down"], pixels: int = 300) -> None:
        """Scroll the page in a bounded direction."""

        amount = abs(pixels) if direction == "down" else -abs(pixels)
        await self.page.mouse.wheel(0, amount)

    async def hover(self, element: ElementRef) -> None:
        """Resolve and hover an agent-owned element reference."""

        await self._locator(element).hover()

    async def go_back(self) -> None:
        """Navigate one entry backward in the page history."""

        await self.page.go_back(wait_until="domcontentloaded")

    async def go_forward(self) -> None:
        """Navigate one entry forward in the page history."""

        await self.page.go_forward(wait_until="domcontentloaded")

    async def refresh(self) -> None:
        """Reload the active page."""

        await self.page.reload(wait_until="domcontentloaded")

    async def wait(self, ms: int) -> None:
        """Wait for a non-negative number of milliseconds."""

        await self.page.wait_for_timeout(max(0, ms))

    async def execute_action(
        self,
        decision: ActionDecision,
        elements: list[ElementRef],
    ) -> str | None:
        """Route an action decision and return an error string on execution failure."""

        try:
            action = decision.action_type
            if action is ActionType.CLICK:
                await self.click(self._find_element(decision, elements))
            elif action is ActionType.FILL:
                if decision.value is None:
                    return "fill action requires value"
                await self.fill(self._find_element(decision, elements), decision.value)
            elif action is ActionType.SELECT_OPTION:
                if decision.value is None:
                    return "select_option action requires value"
                await self.select_option(self._find_element(decision, elements), decision.value)
            elif action is ActionType.PRESS_KEY:
                key = decision.key or decision.value
                if key is None:
                    return "press_key action requires key"
                await self.press_key(key)
            elif action is ActionType.SCROLL:
                direction = (decision.value or "down").lower()
                if direction not in {"up", "down"}:
                    return f"unsupported scroll direction: {direction}"
                await self.scroll(direction)  # type: ignore[arg-type]
            elif action is ActionType.NAVIGATE:
                if decision.value is None:
                    return "navigate action requires URL in value"
                await self.navigate(decision.value)
            elif action is ActionType.HOVER:
                await self.hover(self._find_element(decision, elements))
            elif action is ActionType.GO_BACK:
                await self.go_back()
            elif action is ActionType.GO_FORWARD:
                await self.go_forward()
            elif action is ActionType.REFRESH:
                await self.refresh()
            elif action is ActionType.WAIT:
                try:
                    wait_ms = int(decision.value or "500")
                except ValueError:
                    return "wait action value must be an integer number of milliseconds"
                await self.wait(wait_ms)
            elif action in {ActionType.DONE, ActionType.STUCK}:
                return None
            return None
        except Exception as exc:  # noqa: BLE001  # Browser errors become step-level data.
            return f"{type(exc).__name__}: {exc}"

    async def get_page_url(self) -> str:
        return self.page.url

    async def get_page_title(self) -> str:
        return await self.page.title()

    async def take_screenshot(self, save_path: Path) -> bytes:
        """Capture a screenshot and persist it for later evidence references."""

        save_path.parent.mkdir(parents=True, exist_ok=True)
        return await self.page.screenshot(path=str(save_path), full_page=False)

    async def get_accessibility_snapshot(self) -> dict[str, Any]:
        """Return a compact semantic tree with selectors for interactive nodes.

        Newer Playwright versions expose ``Locator.aria_snapshot`` as YAML rather
        than the older ``page.accessibility.snapshot`` dictionary. The returned
        structure keeps that raw YAML and adds deterministic DOM-backed locator
        values so the perception layer can create resolvable ``ElementRef`` objects.
        """

        aria_snapshot = await self.page.locator("body").aria_snapshot()
        nodes = await self.page.evaluate(_ACCESSIBLE_NODES_SCRIPT)
        return {
            "role": "document",
            "name": await self.get_page_title(),
            "children": nodes,
            "aria_snapshot": aria_snapshot,
        }

    async def get_console_entries(self) -> list[ConsoleEntry]:
        """Drain console messages captured since the previous observation."""

        entries = list(self._console_buffer)
        self._console_buffer.clear()
        return entries

    async def get_network_entries(self) -> list[NetworkEntry]:
        """Drain network events captured since the previous observation."""

        entries = list(self._network_buffer)
        self._network_buffer.clear()
        return entries

    def _locator(self, element: ElementRef) -> Any:
        return self.page.locator(element.locator_value)

    @staticmethod
    def _find_element(decision: ActionDecision, elements: list[ElementRef]) -> ElementRef:
        if decision.ref is None:
            raise ValueError(f"{decision.action_type.value} action requires element ref")
        for element in elements:
            if element.id == decision.ref:
                return element
        raise ValueError(f"unknown element ref: {decision.ref}")

    def _on_console(self, message: Any) -> None:
        level = str(message.type).lower()
        if level == "warning":
            level = "warn"
        if level not in {"log", "warn", "error", "info", "debug"}:
            level = "log"
        self._console_buffer.append(
            ConsoleEntry(level=level, text=message.text, timestamp=datetime.now(UTC))
        )

    def _on_request(self, request: Any) -> None:
        self._request_started[id(request)] = time.perf_counter()

    async def _on_response(self, response: Any) -> None:
        request = response.request
        duration_ms = _duration_ms(self._request_started.pop(id(request), None))
        preview = await _response_preview(response)
        status = int(response.status)
        self._network_buffer.append(
            NetworkEntry(
                method=request.method,
                url=response.url,
                status=status,
                response_body_preview=preview,
                duration_ms=duration_ms,
                is_failure=status >= 400,
            )
        )

    async def _on_request_failed(self, request: Any) -> None:
        duration_ms = _duration_ms(self._request_started.pop(id(request), None))
        self._network_buffer.append(
            NetworkEntry(
                method=request.method,
                url=request.url,
                status=None,
                response_body_preview=None,
                duration_ms=duration_ms,
                is_failure=True,
            )
        )

    async def _close_context_without_trace(self) -> None:
        if self._context is None:
            return
        context = self._context
        try:
            await context.close()
        finally:
            self._context = None
            self._page = None
            self._console_buffer.clear()
            self._network_buffer.clear()
            self._request_started.clear()


def _duration_ms(start: float | None) -> int | None:
    if start is None:
        return None
    return round((time.perf_counter() - start) * 1000)


async def _response_preview(response: Any) -> str | None:
    try:
        content_type = (await response.header_value("content-type") or "").lower()
        if not any(kind in content_type for kind in ("json", "text", "javascript", "xml")):
            return None
        return str(await response.text())[:500]
    except PlaywrightError:
        return None


_ACCESSIBLE_NODES_SCRIPT = r"""
() => {
  const selectors = [
    'a', 'button', 'input', 'textarea', 'select', 'summary',
    '[role]', '[contenteditable="true"]',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'nav', 'main', 'form'
  ].join(',');

  const visible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      rect.width > 0 && rect.height > 0;
  };

  const cssPath = (element) => {
    if (element.id) return `#${CSS.escape(element.id)}`;
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.body) {
      let part = current.tagName.toLowerCase();
      let index = 1;
      let sibling = current;
      while ((sibling = sibling.previousElementSibling)) {
        if (sibling.tagName === current.tagName) index += 1;
      }
      part += `:nth-of-type(${index})`;
      parts.unshift(part);
      current = current.parentElement;
    }
    return 'body > ' + parts.join(' > ');
  };

  const roleFor = (element) => {
    const explicit = element.getAttribute('role');
    if (explicit) return explicit;
    const tag = element.tagName.toLowerCase();
    if (tag === 'a' && element.hasAttribute('href')) return 'link';
    if (tag === 'button' || element.getAttribute('type') === 'button' ||
        element.getAttribute('type') === 'submit') return 'button';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'input') {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      return 'textbox';
    }
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'nav') return 'navigation';
    if (tag === 'main') return 'main';
    if (tag === 'form') return 'form';
    return 'generic';
  };

  const nameFor = (element) => {
    const labelledBy = element.getAttribute('aria-labelledby');
    if (labelledBy) {
      return labelledBy.split(/\s+/).map(id => document.getElementById(id)?.innerText || '')
        .join(' ').trim();
    }
    const ariaLabel = element.getAttribute('aria-label');
    if (ariaLabel) return ariaLabel.trim();
    if (element.labels && element.labels.length) return element.labels[0].innerText.trim();
    return (element.innerText || element.value || element.getAttribute('placeholder') ||
      element.getAttribute('title') || '').trim().replace(/\s+/g, ' ').slice(0, 200);
  };

  return Array.from(document.querySelectorAll(selectors))
    .filter(element => visible(element))
    .map(element => ({
      role: roleFor(element),
      name: nameFor(element),
      locator_value: cssPath(element),
      children: []
    }));
}
"""
