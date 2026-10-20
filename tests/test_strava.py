from unittest.mock import Mock

import pytest

from hevy2garmin.strava import StravaReviewRequired, StravaUploader, build_strength_payload


def payload(workout=None, hr=None, mappings=None):
    return build_strength_payload(
        workout or {"start_time": "2026-10-20T13:36:00Z",
                    "end_time": "2026-10-20T14:10:14Z", "exercises": [
                        {"title": "Chest Press (Machine)", "exercise_template_id": "chest",
                         "sets": [{"reps": 15, "weight_kg": 27.2155422}]}]},
        [{"time": 0, "hr": 103}, {"time": 120, "hr": 143}] if hr is None else hr,
        {"chest": "MACHINE_CHEST_PRESS"} if mappings is None else mappings,
        timezone_name="America/Toronto", supported_exercises={"MACHINE_CHEST_PRESS"},
        calories=166,
    )


def test_structured_sets_and_real_hr_share_one_payload():
    result = payload()
    assert result["sets"] == [{"exercise_type": "MACHINE_CHEST_PRESS",
                               "repetitions": 15, "weight": 27.2155422}]
    assert result["streams"] == {"time": [0, 120], "heartrate": [103, 143]}
    assert result["utc_offset"] == -14400
    assert result["elapsed_time"] == 2054
    assert result["total_calories"] == 166
    assert "start_time" not in result["sets"][0]  # Hevy supplies no real per-set timing


def test_missing_hr_defers_instead_of_inventing_it():
    with pytest.raises(StravaReviewRequired):
        payload(hr=[])


def test_unknown_exercises_fail_instead_of_becoming_wrong_exercises():
    with pytest.raises(StravaReviewRequired):
        payload(mappings={})


def test_invalid_and_out_of_window_hr_are_excluded():
    result = payload(hr=[{"time": -1, "hr": 80}, {"time": 3, "hr": 103},
                         {"time": 2055, "hr": 120}, {"time": 4, "hr": 0}])
    assert result["streams"] == {"time": [3], "heartrate": [103]}


def uploader():
    cache = {}
    store = Mock()
    store.get_app_config.side_effect = cache.get
    store.set_app_config.side_effect = cache.__setitem__
    session = Mock()
    session.post.return_value.json.return_value = {"id": 42}
    session.get.return_value.json.return_value = {"activity_id": 123}
    return StravaUploader(session, store), session, cache


def test_successful_upload_is_weight_training_and_not_reposted():
    client, session, _ = uploader()
    workout = {"id": "upper-a", "title": "Upper A"}
    assert client.upload(workout, payload())["activity_id"] == 123
    assert client.upload(workout, payload())["activity_id"] == 123
    session.post.assert_called_once()
    assert session.post.call_args.kwargs["data"]["sport_type"] == "WeightTraining"


def test_processing_receipt_is_polled_on_next_run_without_reuploading():
    client, session, _ = uploader()
    session.get.return_value.json.side_effect = [{"status": "processing"}, {"activity_id": 123}]
    assert client.upload({"id": "a"}, payload())["status"] == "processing"
    assert client.upload({"id": "a"}, payload())["activity_id"] == 123
    session.post.assert_called_once()


def test_post_timeout_never_blindly_retries():
    client, session, _ = uploader()
    session.post.side_effect = TimeoutError("timeout")
    with pytest.raises(TimeoutError):
        client.upload({"id": "a"}, payload())
    with pytest.raises(StravaReviewRequired):
        client.upload({"id": "a"}, payload())
    session.post.assert_called_once()


def test_error_receipt_is_not_success_or_another_upload():
    client, session, _ = uploader()
    session.get.return_value.json.return_value = {"error": "duplicate"}
    for _ in range(2):
        with pytest.raises(StravaReviewRequired):
            client.upload({"id": "a"}, payload())
    session.post.assert_called_once()
