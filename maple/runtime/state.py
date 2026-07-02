"""Helpers for AnalysisState mutation."""
from maple.models import AnalysisState, AuditEntry


def add_audit(state: AnalysisState, agent: str, message: str, detail: str = "") -> None:
    """Append an audit trail entry to the state."""
    state.audit_trail.append(AuditEntry(agent=agent, message=message, detail=detail))
