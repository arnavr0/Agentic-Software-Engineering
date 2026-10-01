"""Convert browser state into compact, agent-owned page observations."""

import json
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol

from playwright.async_api import Error as PlaywrightError

from agentic_tester.browser.worker import BrowserWorker
from agentic_tester.models.element import ElementRef
from agentic_tester.models.observation import PageObservation
from agentic_tester.models.state import StateFingerprint


class ScreenshotStore(Protocol):
    """The small ArtifactStore surface needed by perception."""

    async def save_screenshot(
        self,
        session_id: str,
        mission_id: str,
        step_index: int,
        data: bytes,
    ) -> str:
        """Persist screenshot bytes and return the report-relative path."""


class PerceptionEngine:
    """Build ``PageObservation`` objects and map semantic nodes to ``ElementRef``."""

    _INTERACTIVE_ROLES: ClassVar[set[str]] = {
        "button",
        "checkbox",
        "combobox",
        "link",
        "listbox",
        "menuitem",
        "option",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
        "treeitem",
    }

    def __init__(self) -> None:
        self._element_counter = 0

    async def capture(
        self,
        browser: BrowserWorker,
        artifact_store: ScreenshotStore,
        session_id: str,
        mission_id: str,
        step_index: int,
    ) -> PageObservation:
        """Capture screenshot, semantic state, text, console, network, and fingerprint."""

        self.reset_counter()
        screenshot_bytes = await browser.page.screenshot(full_page=False)
        screenshot_path = await artifact_store.save_screenshot(
            session_id,
            mission_id,
            step_index,
            screenshot_bytes,
        )
        snapshot = await browser.get_accessibility_snapshot()
        element_refs, yaml_tree = self.map_to_element_refs(snapshot)
        try:
            visible_text = await browser.page.locator("body").inner_text(timeout=2_000)
        except PlaywrightError:
            visible_text = ""
        return PageObservation(
            url=await browser.get_page_url(),
            title=await browser.get_page_title(),
            screenshot_path=screenshot_path,
            accessibility_tree=yaml_tree,
            interactive_elements=element_refs,
            visible_text_summary=" ".join(visible_text.split())[:2_000],
            console_entries=await browser.get_console_entries(),
            network_log=await browser.get_network_entries(),
            state_fingerprint=StateFingerprint.compute(
                await browser.get_page_url(),
                [element.name for element in element_refs],
            ),
            timestamp=datetime.now(UTC),
        )

    def map_to_element_refs(self, a11y_snapshot: dict[str, Any]) -> tuple[list[ElementRef], str]:
        """Walk a semantic tree and return agent-owned refs plus an LLM-friendly YAML view."""

        elements: list[ElementRef] = []
        lines: list[str] = []

        def walk(node: dict[str, Any], depth: int) -> None:
            role = str(node.get("role", "generic"))
            name = str(node.get("name", ""))
            ref_id: str | None = None
            if role in self._INTERACTIVE_ROLES:
                ref_id = f"el_{self._element_counter}"
                self._element_counter += 1
                locator_value = node.get("locator_value")
                if not isinstance(locator_value, str) or not locator_value:
                    locator_value = str(node.get("ref", ""))
                elements.append(
                    ElementRef(
                        id=ref_id,
                        role=role,
                        name=name,
                        locator_value=locator_value,
                        is_enabled=bool(node.get("is_enabled", True)),
                        value=_optional_string(node.get("value")),
                    )
                )
            lines.append(_yaml_line(role, name, ref_id, depth))
            children = node.get("children", [])
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        walk(child, depth + 1)

        walk(a11y_snapshot, 0)
        raw_aria = a11y_snapshot.get("aria_snapshot")
        if isinstance(raw_aria, str) and raw_aria.strip():
            lines.append("# Raw Playwright ARIA snapshot:")
            lines.extend(f"# {line}" for line in raw_aria.splitlines())
        return elements, "\n".join(lines)

    def reset_counter(self) -> None:
        """Reset element IDs before capturing a new page observation."""

        self._element_counter = 0


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _yaml_line(role: str, name: str, ref_id: str | None, depth: int) -> str:
    label = f"{role} {json.dumps(name)}" if name else role
    if ref_id is not None:
        label += f" [{ref_id}]"
    return f"{'  ' * depth}- {label}"
