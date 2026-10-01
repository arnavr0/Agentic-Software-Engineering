"""Agent-owned browser element identities."""

from pydantic import BaseModel


class ElementRef(BaseModel):
    """Stable identity exposed to the LLM and resolved by the browser worker."""

    id: str
    role: str
    name: str
    locator_value: str
    is_enabled: bool = True
    value: str | None = None
