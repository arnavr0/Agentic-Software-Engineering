"""Deterministic guardrail policy tests."""

from agentic_tester.agent.action_space import ActionDecision, ActionType
from agentic_tester.agent.guardrails import GuardrailsEngine
from agentic_tester.models.element import ElementRef
from agentic_tester.models.safety import ActionRisk, ExecutionPolicy


def _action(action_type: ActionType, **kwargs) -> ActionDecision:
    return ActionDecision(
        action_type=action_type,
        reasoning="test",
        confidence=1.0,
        expected_result="test result",
        **kwargs,
    )


def _element(name: str, ref: str = "el_0") -> ElementRef:
    return ElementRef(id=ref, role="button", name=name, locator_value="#control")


def test_classification_is_deterministic_and_risk_aware() -> None:
    engine = GuardrailsEngine(ExecutionPolicy())

    assert engine.classify_risk(_action(ActionType.SCROLL), None) is ActionRisk.READ
    assert engine.classify_risk(_action(ActionType.NAVIGATE, value="/projects"), None) is ActionRisk.NAVIGATION
    assert (
        engine.classify_risk(_action(ActionType.CLICK, ref="el_0"), _element("Delete account"))
        is ActionRisk.DESTRUCTIVE_MUTATION
    )
    assert (
        engine.classify_risk(_action(ActionType.CLICK, ref="el_0"), _element("Send invite"))
        is ActionRisk.EXTERNAL_SIDE_EFFECT
    )


def test_safe_action_is_allowed_and_unsafe_element_is_blocked() -> None:
    engine = GuardrailsEngine(ExecutionPolicy())
    safe = engine.check(
        _action(ActionType.CLICK, ref="el_0"),
        "http://localhost:3000/projects",
        _element("Save project"),
        "http://localhost:3000",
    )
    blocked = engine.check(
        _action(ActionType.CLICK, ref="el_0"),
        "http://localhost:3000/projects",
        _element("Delete account"),
        "http://localhost:3000",
    )

    assert safe.allowed is True
    assert blocked.allowed is False
    assert blocked.risk_level is ActionRisk.DESTRUCTIVE_MUTATION


def test_url_input_and_domain_boundaries_are_enforced() -> None:
    engine = GuardrailsEngine(ExecutionPolicy())
    external = engine.check(
        _action(ActionType.NAVIGATE, value="https://example.com"),
        "http://localhost:3000/projects",
        None,
        "http://localhost:3000",
    )
    blocked_url = engine.check(
        _action(ActionType.NAVIGATE, value="/billing/checkout"),
        "http://localhost:3000/projects",
        None,
        "http://localhost:3000",
    )
    blocked_input = engine.check(
        _action(ActionType.FILL, ref="el_0", value="<script>alert(1)</script>"),
        "http://localhost:3000/projects",
        _element("Project name"),
        "http://localhost:3000",
    )

    assert external.allowed is False
    assert "navigation away" in (external.reason or "")
    assert blocked_url.allowed is False
    assert "blocked pattern" in (blocked_url.reason or "")
    assert blocked_input.allowed is False


def test_repeat_and_consecutive_error_limits_trip_without_llm() -> None:
    policy = ExecutionPolicy(max_same_action_repeats=2, max_consecutive_errors=2)
    engine = GuardrailsEngine(policy)
    action = _action(ActionType.WAIT, value="10")
    url = "http://localhost:3000"

    assert engine.check(action, url, None, url).allowed is True
    assert engine.check(action, url, None, url).allowed is True
    repeated = engine.check(action, url, None, url)
    assert repeated.allowed is False
    assert "repeated action" in (repeated.reason or "")

    assert engine.record_error() is False
    assert engine.record_error() is True
    circuit_breaker = engine.check(
        _action(ActionType.SCROLL),
        url,
        None,
        url,
    )
    assert circuit_breaker.allowed is False
    engine.record_success()
    assert engine.check(_action(ActionType.SCROLL), url, None, url).allowed is True
