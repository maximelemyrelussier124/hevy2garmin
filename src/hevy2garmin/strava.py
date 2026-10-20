"""Structured strength uploads with real heart-rate samples.

Strava upload schema: https://developers.strava.com/docs/uploads/
This is a separate destination: a Garmin exerciseSets edit is not a Strava sync.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class StravaReviewRequired(RuntimeError):
    """Stop instead of guessing exercise identities or duplicating an upload."""


def build_strength_payload(workout, hr_samples, exercise_types, *, timezone_name,
                           calories=None, supported_exercises):
    """Build one WeightTraining JSON file; never synthesize HR or set times.

    exercise_types maps Hevy template IDs (preferred) or exact titles to Strava
    identifiers. Unknown exercises fail the whole workout instead of silently
    becoming an incorrect generic exercise. Hevy weights are already kilograms.
    """
    def parse(value):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("Workout timestamps must include a timezone")
        return result.astimezone(timezone.utc)

    start = parse(workout["start_time"])
    end = parse(workout["end_time"])
    elapsed = round((end - start).total_seconds())
    if elapsed <= 0:
        raise ValueError("Workout must have positive elapsed time")
    sets = []
    for exercise in workout.get("exercises", []):
        name = exercise.get("title", "")
        identifier = (exercise_types.get(exercise.get("exercise_template_id"))
                      or exercise_types.get(name))
        if identifier not in supported_exercises:
            raise StravaReviewRequired(f"Strava exercise mapping required: {name}")
        for raw in exercise.get("sets", []):
            item = {"exercise_type": identifier}
            reps = raw.get("reps")
            weight = raw.get("weight_kg")
            duration = raw.get("duration_seconds")
            if reps is not None:
                if isinstance(reps, bool) or int(reps) != reps or reps < 0:
                    raise ValueError("Invalid repetitions")
                item["repetitions"] = int(reps)
            if weight is not None:
                if not math.isfinite(float(weight)) or weight < 0:
                    raise ValueError("Invalid weight")
                item["weight"] = float(weight)
            if duration is not None:
                if not math.isfinite(float(duration)) or duration <= 0:
                    raise ValueError("Invalid set duration")
                item["duration"] = max(1, round(duration))
            if reps is None and duration is None:
                raise StravaReviewRequired(f"No repetitions or duration for {name}")
            sets.append(item)
    if not sets:
        raise ValueError("Workout has no sets")
    samples = {}
    for sample in hr_samples or []:
        offset, bpm = float(sample["time"]), float(sample["hr"])
        if not math.isfinite(offset) or not math.isfinite(bpm):
            continue
        if 0 <= offset <= elapsed and 0 < bpm < 256:
            samples[round(offset)] = round(bpm)
    if not samples:
        raise StravaReviewRequired("Real heart-rate samples required; defer upload")
    times = sorted(samples)
    result = {
        "version": "1.0",
        "start_time": start.isoformat(),
        "utc_offset": int(start.astimezone(ZoneInfo(timezone_name)).utcoffset().total_seconds()),
        "elapsed_time": elapsed,
        "creator": {"name": "Hevy2Garmin"},
        "sets": sets,
        "streams": {"time": times, "heartrate": [samples[t] for t in times]},
    }
    if calories is not None:
        if not math.isfinite(float(calories)) or calories < 0:
            raise ValueError("Invalid calories")
        result["total_calories"] = round(calories)
    return result


class StravaUploader:
    """Persist receipts before polling; uncertain POSTs require reconciliation.

    Use a single-worker queue per account. `store` supplies get_app_config and
    set_app_config, as provided by the project's Postgres/SQLite backends.
    Refresh/access tokens belong in the existing secret store, never upload logs.
    """

    def __init__(self, session, store):
        self.session = session
        self.store = store

    def upload(self, workout, payload):
        key = f"strava_upload_{workout['id']}"
        state = self.store.get_app_config(key) or {}
        if state.get("activity_id"):
            return state
        if state.get("status") in {"submitting", "uncertain"}:
            raise StravaReviewRequired("Upload needs reconciliation before another POST")
        if not state.get("upload_id"):
            # Write intent before sending: a crash/timeout must not create another
            # upload on the next scheduled run. external_id is not an idempotency key.
            self.store.set_app_config(key, {"status": "submitting"})
            try:
                response = self.session.post(
                    "https://www.strava.com/api/v3/uploads",
                    data={"data_type": "json", "sport_type": "WeightTraining",
                          "name": workout.get("title", "Weight Training"),
                          "external_id": f"hevy-{workout['id']}.json"},
                    files={"file": (f"hevy-{workout['id']}.json",
                                     json.dumps(payload, allow_nan=False), "application/json")},
                    timeout=60,
                )
                response.raise_for_status()
                receipt = response.json()
                state = {"status": "processing", "upload_id": receipt["id"]}
            except Exception:
                self.store.set_app_config(key, {"status": "uncertain"})
                raise
            self.store.set_app_config(key, state)
        response = self.session.get(
            f"https://www.strava.com/api/v3/uploads/{int(state['upload_id'])}", timeout=30
        )
        response.raise_for_status()
        receipt = response.json()
        if receipt.get("error"):
            error = str(receipt["error"])[:500]
            self.store.set_app_config(key, {**state, "status": "rejected", "error": error})
            raise StravaReviewRequired(f"Strava rejected the upload: {error}")
        if receipt.get("activity_id"):
            state = {**state, "status": "complete", "activity_id": receipt["activity_id"]}
            self.store.set_app_config(key, state)
        return state
