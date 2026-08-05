from datetime import UTC, datetime, timedelta

from nowcast_service.alerts.engine import AlertEngine, AlertEventType
from nowcast_service.decision_engine import Action, DataQuality, EquipmentState, RiskState, SafetyDecision
from nowcast_service.runtime.models import DecisionSnapshot


def snapshot(state: RiskState, reasons: tuple[str, ...]) -> DecisionSnapshot:
    return DecisionSnapshot.create(evaluation_at=datetime.now(UTC), algorithm_version="test",
        configuration_version="1", equipment_state=EquipmentState.NOT_DEPLOYED,
        decision=SafetyDecision(state=state, data_quality=DataQuality.COMPLETE,
            action=Action.DO_NOT_SETUP if state is RiskState.RED else Action.WAIT_AND_RECHECK,
            reason_codes=reasons, reasons=reasons), active_hazards=(), source_snapshots=())


def test_alarm_transitions_ack_and_repeat() -> None:
    engine = AlertEngine(red_repeat_seconds=30, yellow_repeat_seconds=60)
    yellow, red = snapshot(RiskState.YELLOW, ("Y",)), snapshot(RiskState.RED, ("R",))
    assert engine.evaluate(None, yellow).event_type is AlertEventType.YELLOW
    assert engine.evaluate(yellow, red).event_type is AlertEventType.ESCALATED
    current = engine.current
    repeated = engine.repeat_if_due(red, now=current.created_at + timedelta(seconds=30))
    assert repeated.event_type is AlertEventType.REPEAT
    event = engine.acknowledge(red)
    assert event.event_type is AlertEventType.ACKNOWLEDGED
    assert red.decision.state is RiskState.RED
