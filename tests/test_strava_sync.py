from unittest.mock import Mock

import pytest

from hevy2garmin.strava import StravaReviewRequired
from hevy2garmin.strava_sync import exercise_mappings, matching_activities, prepare_replacement


def test_machine_equipment_is_not_misidentified_as_smith_machine():
    mappings = exercise_mappings({}, {})
    assert mappings["7EB3F7C3"] == "MACHINE_CHEST_PRESS"
    assert mappings["9237BAD1"] == "MACHINE_SEATED_SHOULDER_PRESS"
    assert mappings.get("unknown-exercise") is None
    assert mappings["Rowing Barre T Buste Appuyé"] == "T_BAR_ROW"
    assert mappings["Développé Épaules Assis (Machine)"] == "MACHINE_SEATED_SHOULDER_PRESS"


def test_matching_excludes_other_sports_and_wrong_time_or_duration():
    session = Mock()
    base = {"id": 1, "sport_type": "WeightTraining", "start_date": "2026-10-20T13:36:00Z", "elapsed_time": 2054}
    session.get.return_value.json.return_value = [base, {**base, "sport_type": "Run"},
        {**base, "start_date": "2026-10-20T14:00:00Z"}, {**base, "elapsed_time": 7200}]
    result = matching_activities(session, {"start_time": "2026-10-20T13:36:00Z", "end_time": "2026-10-20T14:10:14Z"})
    assert result == [base]


def test_replacement_requires_matching_permission_or_garmin_device():
    session, store = Mock(), Mock()
    session.get.return_value.json.return_value = {"device_name": "Hevy"}
    with pytest.raises(StravaReviewRequired):
        prepare_replacement(session, store, {"id": "a"}, {"id": 123}, allowed_id=999, automatic=True)
    session.delete.assert_not_called()


def test_explicit_original_is_backed_up_before_deletion():
    calls = []
    session, store = Mock(), Mock()
    session.get.return_value.json.return_value = {"device_name": "Garmin vivoactive 6"}
    store.set_app_config.side_effect = lambda *args: calls.append("backup")
    session.delete.side_effect = lambda *args, **kwargs: calls.append("delete") or Mock()
    prepare_replacement(session, store, {"id": "a"}, {"id": 123}, allowed_id=123)
    assert calls == ["backup", "delete"]


def test_failed_backup_preserves_original():
    session, store = Mock(), Mock()
    session.get.return_value.json.return_value = {"device_name": "Garmin"}
    store.set_app_config.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        prepare_replacement(session, store, {"id": "a"}, {"id": 123}, allowed_id=123)
    session.delete.assert_not_called()
