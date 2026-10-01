"""Domain models shared by the tester subsystems."""

from .element import ElementRef
from .evidence import Evidence, EvidenceType
from .mission import ActionStep, Checkpoint, ExpectedBehavior, TestMission
from .observation import ConsoleEntry, NetworkEntry, PageObservation
from .results import (
    ArtifactPaths,
    BugReport,
    CheckpointResult,
    ExpectationEvaluation,
    MissionResult,
    ReplayStep,
    SessionReport,
    StepResult,
    TokenUsage,
)
from .safety import ActionRisk, ExecutionPolicy
from .session import TestSession
from .state import StateFingerprint

__all__ = [
    "ActionRisk",
    "ActionStep",
    "ArtifactPaths",
    "BugReport",
    "Checkpoint",
    "CheckpointResult",
    "ConsoleEntry",
    "ElementRef",
    "Evidence",
    "EvidenceType",
    "ExecutionPolicy",
    "ExpectationEvaluation",
    "ExpectedBehavior",
    "MissionResult",
    "NetworkEntry",
    "PageObservation",
    "ReplayStep",
    "SessionReport",
    "StateFingerprint",
    "StepResult",
    "TestMission",
    "TestSession",
    "TokenUsage",
]
