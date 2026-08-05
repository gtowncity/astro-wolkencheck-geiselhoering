from dataclasses import dataclass
from nowcast_service.decision_engine import RiskState
from nowcast_service.sources.cap_runtime import _cap_evidence

@dataclass
class Info:
    event: str
    severity: str
    headline: str | None = None
@dataclass
class Alert:
    info: Info
    def german_info(self) -> Info: return self.info

def test_critical_cap_events_are_red_even_with_minor_severity() -> None:
    assert _cap_evidence((Alert(Info("GEWITTER", "Minor")),))[0].state is RiskState.RED
    assert _cap_evidence((Alert(Info("STARKREGEN", "Minor")),))[0].state is RiskState.RED
    assert _cap_evidence((Alert(Info("WIND", "Moderate")),))[0].state is RiskState.YELLOW
