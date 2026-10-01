"""Deterministic action-risk policy used to constrain the tester."""

from enum import Enum

from pydantic import BaseModel, Field


class ActionRisk(str, Enum):
    """Risk classification for browser actions."""

    READ = "read"
    NAVIGATION = "navigation"
    NON_DESTRUCTIVE_MUTATION = "non_destructive_mutation"
    DESTRUCTIVE_MUTATION = "destructive_mutation"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class ExecutionPolicy(BaseModel):
    """Deterministic execution boundary; unsafe risks are blocked by default."""

    allowed_risks: set[ActionRisk] = Field(
        default_factory=lambda: {
            ActionRisk.READ,
            ActionRisk.NAVIGATION,
            ActionRisk.NON_DESTRUCTIVE_MUTATION,
        }
    )

    allowed_url_patterns: list[str] = Field(default_factory=lambda: ["*"])
    blocked_url_patterns: list[str] = Field(
        default_factory=lambda: [
            "**/admin/delete*",
            "**/api/*/destroy*",
            "**/account/delete*",
            "**/billing*",
            "**/payment*",
            "**/checkout*",
        ]
    )
    blocked_element_patterns: list[str] = Field(
        default_factory=lambda: [
            "*delete*account*",
            "*remove*all*",
            "*drop*database*",
            "*factory*reset*",
            "*erase*",
            "*permanently*delete*",
            "*cancel*subscription*",
            "*close*account*",
        ]
    )
    blocked_input_patterns: list[str] = Field(
        default_factory=lambda: ["DROP TABLE*", "<script>*", "rm -rf*"]
    )

    max_consecutive_errors: int = Field(default=5, gt=0)
    max_same_action_repeats: int = Field(default=3, gt=0)
    allow_file_downloads: bool = False
    allow_new_tabs: bool = False
    allow_navigation_away: bool = False

    destructive_patterns: list[str] = Field(
        default_factory=lambda: [
            "*delete*",
            "*remove*",
            "*destroy*",
            "*drop*",
            "*cancel*",
            "*revoke*",
            "*terminate*",
        ]
    )
    side_effect_patterns: list[str] = Field(
        default_factory=lambda: [
            "*payment*",
            "*purchase*",
            "*subscribe*",
            "*send*email*",
            "*send*invite*",
        ]
    )
