"""Replaceable issuer-verification boundary; the demo adapter never contacts issuers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


SIMULATION_LABEL = "Simulated — not externally verified."


@dataclass(frozen=True)
class SourceCheckResult:
    status: str
    mode: str
    source: str
    detail: str
    label: str
    requires_manual_confirmation: bool


class SourceVerifier(Protocol):
    """Provider contract for a future organization-authorized issuer connector."""

    def verify(self, reference: str | None) -> SourceCheckResult: ...


class SimulatedSourceVerifier:
    """Synthetic-only demo adapter that always leaves verification pending."""

    name = "External source connector"

    def verify(self, reference: str | None) -> SourceCheckResult:
        if reference and reference.strip():
            summary = f"Synthetic source response prepared for reference {reference.strip()}; no external source was contacted."
        else:
            summary = "No certificate reference was extracted; no external source was contacted."
        return SourceCheckResult(
            status="verification_pending",
            mode="simulated",
            source="Simulated issuer reference (no external authority contacted)",
            detail=f"{SIMULATION_LABEL} {summary} Manual confirmation is required.",
            label=SIMULATION_LABEL,
            requires_manual_confirmation=True,
        )
