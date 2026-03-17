from datetime import datetime, timedelta

from app.diagnosis.models import DiagnosisSessionCreate, FaultWindow
from app.diagnosis.session_manager import (
    DiagnosisSessionManager,
    _normalize_session_status_value,
)


def test_normalize_session_status_value_maps_legacy_need_next_round():
    assert _normalize_session_status_value('need_next_round') == 'ready_for_next_probe'
    assert _normalize_session_status_value('ready_for_next_probe') == 'ready_for_next_probe'


def test_session_manager_set_status_normalizes_legacy_status(tmp_path):
    manager = DiagnosisSessionManager(output_root=str(tmp_path))
    session = manager.create_session(
        DiagnosisSessionCreate(
            question='q',
            fault_window=FaultWindow(
                start_at=datetime.now(),
                end_at=datetime.now() + timedelta(minutes=1),
            ),
            devices=[],
        )
    )
    updated = manager.set_status(session.session_id, 'need_next_round')
    assert updated is not None
    assert updated.status == 'ready_for_next_probe'
    loaded = manager.get_session(session.session_id)
    assert loaded is not None
    assert loaded.status == 'ready_for_next_probe'
