from nowcast_service.decision_engine import (
    Action,
    DataQuality,
    EquipmentState,
    Evidence,
    RiskState,
    SourceHealth,
    SourceState,
    evaluate_safety,
)


def core_sources(
    *, radar: SourceState = SourceState.LIVE, cap: SourceState = SourceState.LIVE
) -> tuple[SourceHealth, ...]:
    return (
        SourceHealth(source="DWD_RV", required=True, state=radar),
        SourceHealth(source="DWD_CAP", required=True, state=cap),
    )


def test_red_hazard_wins_even_when_required_source_failed() -> None:
    decision = evaluate_safety(
        evidence=(
            Evidence(
                source="RAIN_SENSOR",
                state=RiskState.RED,
                reason_code="SENSOR_WET",
                reason="Regensensor meldet nass.",
                latched=True,
            ),
        ),
        source_health=core_sources(radar=SourceState.FAILED),
        equipment_state=EquipmentState.DEPLOYED_ATTENDED,
    )

    assert decision.state is RiskState.RED
    assert decision.data_quality is DataQuality.INSUFFICIENT
    assert decision.action is Action.STOP_AND_PROTECT


def test_yellow_hazard_is_not_hidden_by_unknown_data() -> None:
    decision = evaluate_safety(
        evidence=(
            Evidence(
                source="DWD_RV",
                state=RiskState.YELLOW,
                reason_code="RADAR_ARRIVAL_61_TO_120_MIN",
                reason="Niederschlag koennte in 90 Minuten eintreffen.",
            ),
        ),
        source_health=core_sources(cap=SourceState.FAILED),
        equipment_state=EquipmentState.NOT_DEPLOYED,
    )

    assert decision.state is RiskState.YELLOW
    assert decision.data_quality is DataQuality.INSUFFICIENT
    assert decision.action is Action.WAIT_AND_RECHECK


def test_missing_required_source_prevents_green_and_explains_why() -> None:
    decision = evaluate_safety(
        evidence=(),
        source_health=core_sources(cap=SourceState.FAILED),
        equipment_state=EquipmentState.NOT_DEPLOYED,
    )

    assert decision.state is RiskState.UNKNOWN
    assert decision.action is Action.UNKNOWN_DO_NOT_RELY
    assert decision.reason_codes == ("SOURCE_DWD_CAP_FAILED",)


def test_no_required_sources_is_never_complete_or_green() -> None:
    decision = evaluate_safety(
        evidence=(),
        source_health=(),
        equipment_state=EquipmentState.NOT_DEPLOYED,
    )

    assert decision.state is RiskState.UNKNOWN
    assert decision.data_quality is DataQuality.INSUFFICIENT


def test_green_requires_healthy_core_sources_and_no_hazard() -> None:
    decision = evaluate_safety(
        evidence=(),
        source_health=core_sources(),
        equipment_state=EquipmentState.NOT_DEPLOYED,
    )

    assert decision.state is RiskState.GREEN
    assert decision.data_quality is DataQuality.COMPLETE
    assert decision.action is Action.NO_LIVE_VETO_DETECTED


def test_optional_disabled_source_does_not_degrade_data_quality() -> None:
    sources = core_sources() + (
        SourceHealth(
            source="RAIN_SENSOR",
            required=False,
            supporting=True,
            state=SourceState.DISABLED,
        ),
    )

    decision = evaluate_safety(
        evidence=(),
        source_health=sources,
        equipment_state=EquipmentState.NOT_DEPLOYED,
    )

    assert decision.state is RiskState.GREEN
    assert decision.data_quality is DataQuality.COMPLETE


def test_failed_supporting_source_degrades_but_does_not_block_green() -> None:
    sources = core_sources() + (
        SourceHealth(
            source="DWD_WN",
            required=False,
            supporting=True,
            state=SourceState.FAILED,
        ),
    )

    decision = evaluate_safety(
        evidence=(),
        source_health=sources,
        equipment_state=EquipmentState.NOT_DEPLOYED,
    )

    assert decision.state is RiskState.GREEN
    assert decision.data_quality is DataQuality.DEGRADED


def test_red_action_depends_on_equipment_state() -> None:
    hazard = (
        Evidence(
            source="DWD_RV",
            state=RiskState.RED,
            reason_code="RADAR_ARRIVAL_WITHIN_60_MIN",
            reason="Niederschlag erreicht den Standort in etwa 43 Minuten.",
        ),
    )

    not_deployed = evaluate_safety(
        evidence=hazard,
        source_health=core_sources(),
        equipment_state=EquipmentState.NOT_DEPLOYED,
    )
    deployed = evaluate_safety(
        evidence=hazard,
        source_health=core_sources(),
        equipment_state=EquipmentState.DEPLOYED_UNATTENDED,
    )

    assert not_deployed.action is Action.DO_NOT_SETUP
    assert deployed.action is Action.STOP_AND_PROTECT


def test_unknown_equipment_state_demands_immediate_check_for_unknown_weather() -> None:
    decision = evaluate_safety(
        evidence=(),
        source_health=core_sources(radar=SourceState.STALE),
        equipment_state=EquipmentState.UNKNOWN,
    )

    assert decision.state is RiskState.UNKNOWN
    assert decision.action is Action.CHECK_EQUIPMENT_IMMEDIATELY
