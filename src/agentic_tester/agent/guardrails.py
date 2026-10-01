"""Deterministic safety checks for proposed browser actions."""

from fnmatch import fnmatchcase
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel

from agentic_tester.agent.action_space import ActionDecision, ActionType
from agentic_tester.logging import get_logger
from agentic_tester.models.element import ElementRef
from agentic_tester.models.safety import ActionRisk, ExecutionPolicy

_LOGGER = get_logger(__name__)


class GuardrailResult(BaseModel):
    """Decision returned by the deterministic safety filter."""

    allowed: bool
    reason: str | None = None
    risk_level: ActionRisk | None = None


class GuardrailsEngine:
    """Apply an ``ExecutionPolicy`` without consulting an LLM."""

    def __init__(self, policy: ExecutionPolicy) -> None:
        self._policy = policy
        self._consecutive_errors = 0
        self._action_counts: dict[str, int] = {}

    def check(
        self,
        action: ActionDecision,
        current_url: str,
        element: ElementRef | None,
        target_base_url: str,
    ) -> GuardrailResult:
        """Return and log a deterministic allow/block decision."""

        result = self._check(action, current_url, element, target_base_url)
        _LOGGER.info(
            "guardrail_decision",
            action_type=action.action_type.value,
            allowed=result.allowed,
            risk_level=result.risk_level.value if result.risk_level is not None else None,
        )
        return result

    def _check(
        self,
        action: ActionDecision,
        current_url: str,
        element: ElementRef | None,
        target_base_url: str,
    ) -> GuardrailResult:
        """Return an allow/block result for one proposed action.

        Checks are ordered from broad policy boundaries to local input and repeat
        limits. Counts are incremented only after every check passes, so a blocked
        proposal cannot consume the allowed repeat budget.
        """

        risk = self.classify_risk(action, element)
        if risk not in self._policy.allowed_risks:
            return GuardrailResult(
                allowed=False,
                reason=f"risk {risk.value} is not allowed by the execution policy",
                risk_level=risk,
            )

        effective_url = self._effective_url(action, current_url)
        blocked_url_reason = self._blocked_url_reason(effective_url)
        if blocked_url_reason is not None:
            return GuardrailResult(allowed=False, reason=blocked_url_reason, risk_level=risk)

        if not self._matches_allowed_url(effective_url):
            return GuardrailResult(
                allowed=False,
                reason=f"URL is outside allowed URL patterns: {effective_url}",
                risk_level=risk,
            )

        if (
            action.action_type is ActionType.NAVIGATE
            and not self._policy.allow_navigation_away
            and not _within_base_url(effective_url, target_base_url)
        ):
            return GuardrailResult(
                allowed=False,
                reason=f"navigation away from target base URL is blocked: {effective_url}",
                risk_level=risk,
            )

        if element is not None and self._matches_any(
            element.name, self._policy.blocked_element_patterns
        ):
            return GuardrailResult(
                allowed=False,
                reason=f"element is blocked by policy: {element.name}",
                risk_level=risk,
            )

        input_values = [value for value in (action.value, action.key) if value is not None]
        for value in input_values:
            if self._matches_any(value, self._policy.blocked_input_patterns):
                return GuardrailResult(
                    allowed=False,
                    reason="action input is blocked by policy",
                    risk_level=risk,
                )

        if self._consecutive_errors >= self._policy.max_consecutive_errors:
            return GuardrailResult(
                allowed=False,
                reason="maximum consecutive browser errors reached",
                risk_level=risk,
            )

        signature = self._action_signature(action)
        repeats = self._action_counts.get(signature, 0)
        if repeats >= self._policy.max_same_action_repeats:
            return GuardrailResult(
                allowed=False,
                reason="maximum repeated action limit reached",
                risk_level=risk,
            )

        self._action_counts[signature] = repeats + 1
        return GuardrailResult(allowed=True, risk_level=risk)

    def classify_risk(self, action: ActionDecision, element: ElementRef | None) -> ActionRisk:
        """Classify an action using only its type and visible element/value text."""

        if action.action_type in {ActionType.SCROLL, ActionType.WAIT, ActionType.DONE, ActionType.STUCK}:
            return ActionRisk.READ
        if action.action_type in {
            ActionType.NAVIGATE,
            ActionType.GO_BACK,
            ActionType.GO_FORWARD,
            ActionType.REFRESH,
        }:
            return ActionRisk.NAVIGATION

        subject = " ".join(
            part for part in (element.name if element is not None else "", action.value or "") if part
        )
        if self._matches_any(subject, self._policy.side_effect_patterns):
            return ActionRisk.EXTERNAL_SIDE_EFFECT
        if self._matches_any(subject, self._policy.destructive_patterns):
            return ActionRisk.DESTRUCTIVE_MUTATION
        return ActionRisk.NON_DESTRUCTIVE_MUTATION

    def record_error(self) -> bool:
        """Record a failed browser action and return whether the limit is reached."""

        self._consecutive_errors += 1
        return self._consecutive_errors >= self._policy.max_consecutive_errors

    def record_success(self) -> None:
        """Reset the consecutive-error circuit breaker after a successful action."""

        self._consecutive_errors = 0

    def reset(self) -> None:
        """Reset mission-local counters before a fresh isolated mission."""

        self._consecutive_errors = 0
        self._action_counts.clear()

    @staticmethod
    def _action_signature(action: ActionDecision) -> str:
        return "|".join(
            (
                action.action_type.value,
                action.ref or "",
                action.value or "",
                action.key or "",
            )
        )

    def _blocked_url_reason(self, url: str) -> str | None:
        for pattern in self._policy.blocked_url_patterns:
            if self._matches(url, pattern):
                return f"URL matches blocked pattern: {pattern}"
        return None

    def _matches_allowed_url(self, url: str) -> bool:
        return any(self._matches(url, pattern) for pattern in self._policy.allowed_url_patterns)

    @staticmethod
    def _matches(value: str, pattern: str) -> bool:
        return fnmatchcase(value.casefold(), pattern.casefold())

    def _matches_any(self, value: str, patterns: list[str]) -> bool:
        return any(self._matches(value, pattern) for pattern in patterns)

    @staticmethod
    def _effective_url(action: ActionDecision, current_url: str) -> str:
        if action.action_type is ActionType.NAVIGATE and action.value:
            return urljoin(current_url, action.value)
        return current_url


def _within_base_url(candidate: str, base: str) -> bool:
    """Check same-origin and base-path containment for explicit navigation."""

    candidate_parts = urlsplit(candidate)
    base_parts = urlsplit(base)
    if not candidate_parts.scheme or not candidate_parts.netloc:
        return False
    candidate_host = candidate_parts.hostname.casefold() if candidate_parts.hostname else None
    base_host = base_parts.hostname.casefold() if base_parts.hostname else None
    if (
        candidate_parts.scheme.casefold() != base_parts.scheme.casefold()
        or candidate_host != base_host
        or candidate_parts.port != base_parts.port
    ):
        return False
    base_path = base_parts.path.rstrip("/")
    if not base_path:
        return True
    candidate_path = candidate_parts.path.rstrip("/") or "/"
    return candidate_path == base_path or candidate_path.startswith(f"{base_path}/")
