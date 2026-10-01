"""Deterministic browser-state fingerprints for replay verification."""

import hashlib


class StateFingerprint:
    """Hash of the observable URL and interactive element names."""

    @staticmethod
    def compute(url: str, element_names: list[str]) -> str:
        """Return a stable 16-character fingerprint for an observable page state."""

        normalized = sorted(set(element_names))
        content = f"{url}|{'|'.join(normalized)}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
