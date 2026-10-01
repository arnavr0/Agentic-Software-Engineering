"""Structured actions proposed by an LLM and executed by the browser worker."""

from enum import Enum

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """Actions available to the tester.

    Risk classification and execution policy checks are implemented in Phase 6;
    this module only defines the transport-safe action vocabulary.
    """

    CLICK = "click"
    FILL = "fill"
    SELECT_OPTION = "select_option"
    PRESS_KEY = "press_key"
    SCROLL = "scroll"
    NAVIGATE = "navigate"
    HOVER = "hover"
    GO_BACK = "go_back"
    GO_FORWARD = "go_forward"
    REFRESH = "refresh"
    WAIT = "wait"
    DONE = "done"
    STUCK = "stuck"


class ActionDecision(BaseModel):
    """The LLM's chosen next browser action."""

    action_type: ActionType
    ref: str | None = None
    value: str | None = None
    key: str | None = None
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    expected_result: str
