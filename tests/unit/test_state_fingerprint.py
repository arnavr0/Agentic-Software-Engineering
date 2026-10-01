"""State fingerprint behavior tests."""

from agentic_tester.models.state import StateFingerprint


def test_fingerprint_is_stable_for_order_and_duplicate_changes() -> None:
    first = StateFingerprint.compute("http://localhost/projects", ["Save", "Name", "Save"])
    second = StateFingerprint.compute("http://localhost/projects", ["Name", "Save"])

    assert first == second
    assert len(first) == 16


def test_fingerprint_changes_when_observable_state_changes() -> None:
    page = StateFingerprint.compute("http://localhost/projects", ["Name"])
    different_url = StateFingerprint.compute("http://localhost/settings", ["Name"])
    different_elements = StateFingerprint.compute("http://localhost/projects", ["Name", "Save"])

    assert page != different_url
    assert page != different_elements
