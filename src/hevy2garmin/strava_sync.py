"""Cloud Strava destination. Run with python -m hevy2garmin.strava_sync.

Disabled until OAuth and explicit automatic replacement consent are saved.
The existing GitHub workflow serializes runs; a DB advisory lock also excludes
manual concurrent runs. Never log API responses containing credentials.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from hevy2garmin.strava import StravaReviewRequired, StravaUploader, build_strength_payload

log = logging.getLogger("hevy2garmin.strava")


def read_credentials(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT credentials FROM platform_credentials WHERE platform='strava'")
        row = cursor.fetchone()
    return row["credentials"] if row else None


def authenticated_session(conn, credentials):
    if credentials.get("expires_at", 0) < time.time() + 300:
        response = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": credentials["client_id"], "client_secret": credentials["client_secret"],
            "refresh_token": credentials["refresh_token"], "grant_type": "refresh_token",
        }, timeout=30)
        response.raise_for_status()
        result = response.json()
        credentials = {**credentials, **{key: result[key] for key in (
            "access_token", "refresh_token", "expires_at")}}
        with conn.cursor() as cursor:
            cursor.execute("UPDATE platform_credentials SET credentials=%s::jsonb WHERE platform='strava'",
                           (json.dumps(credentials),))
        conn.commit()
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {credentials['access_token']}"
    return session


def exercise_mappings(workout, overrides):
    # Garmin's "closest" mapping can identify different equipment (e.g. machine
    # chest press -> Smith press). Do not reuse those approximations for Strava.
    exact = json.loads((Path(__file__).parent / "data/strava_exercise_map.json").read_text())
    return {**exact, **overrides}


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("Timezone required")
    return result


def watch_window(workout, details):
    """Use the device recording's real time window, retaining Hevy's sets."""
    summary = details.get("summaryDTO") or details
    raw = summary.get("startTimeGMT") or details.get("startTimeGMT")
    duration = summary.get("elapsedDuration") or summary.get("duration")
    if not raw or not duration:
        raise StravaReviewRequired("Garmin recording is missing its real start time or duration")
    start = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if start.utcoffset() is None:
        start = start.replace(tzinfo=timezone.utc)  # Garmin explicitly labels this GMT.
    end = start + timedelta(seconds=float(duration))
    return {**workout, "start_time": start.astimezone(timezone.utc).isoformat(),
            "end_time": end.astimezone(timezone.utc).isoformat()}


def matching_activities(session, workout):
    start = parse_time(workout["start_time"]).timestamp()
    duration = (parse_time(workout["end_time"]) - parse_time(workout["start_time"])).total_seconds()
    response = session.get("https://www.strava.com/api/v3/athlete/activities", params={
        "after": int(start - 1201), "before": int(start + 1201), "per_page": 200,
    }, timeout=30)
    response.raise_for_status()
    matches = []
    for activity in response.json():
        if activity.get("sport_type") != "WeightTraining":
            continue
        other_start = parse_time(activity["start_date"]).timestamp()
        other_duration = activity.get("elapsed_time", 0)
        overlap = max(0, min(start + duration, other_start + other_duration) - max(start, other_start))
        if abs(other_start - start) <= 1200 and max(duration, other_duration) > 0:
            if overlap / max(duration, other_duration) >= 0.7:
                matches.append(activity)
    return matches


def prepare_replacement(session, store, workout, existing, *, allowed_id=None, automatic=False):
    """Back up a precisely matched activity before its recoverable deletion."""
    activity_id = int(existing["id"])
    response = session.get(f"https://www.strava.com/api/v3/activities/{activity_id}", timeout=30)
    response.raise_for_status()
    details = response.json()
    explicit = allowed_id is not None and int(allowed_id) == activity_id
    if not explicit and not (automatic and "garmin" in str(details.get("device_name", "")).lower()):
        raise StravaReviewRequired("Existing activity is not an authorized Garmin strength replacement")
    response = session.get(f"https://www.strava.com/api/v3/activities/{activity_id}/streams",
                           params={"keys": "time,heartrate", "key_by_type": "true"}, timeout=30)
    response.raise_for_status()
    store.set_app_config(f"strava_original_{workout['id']}", {
        "activity_id": activity_id, "activity": details, "streams": response.json(),
        "replacement_payload": store.get_app_config(f"strava_payload_{workout['id']}"),
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
    })
    # Strava's public API has no activity deletion endpoint. Preserve this
    # backup and stop. An explicitly authorized one-time replacement can resume
    # after the original is deleted through Strava's normal recoverable UI.
    raise StravaReviewRequired("Original backed up; delete the authorized original in Strava before retrying")


def sync_destination(*, workout_id=None, replace_activity=None, dry_run=False):
    from hevy2garmin.config import load_config
    from hevy2garmin.db import get_db
    from hevy2garmin.garmin import get_client
    from hevy2garmin.hevy import HevyClient
    from hevy2garmin.hr import hr_for_sync
    from hevy2garmin.sync import fetch_workouts

    store = get_db()
    config = store.get_app_config("strava_sync") or {}
    if not workout_id and not config.get("enabled"):
        log.info("Strava publishing disabled; no activity changes")
        return
    if not hasattr(store, "_get_conn"):
        raise ValueError("Strava destination requires the configured cloud Postgres database")
    conn = store._get_conn()
    lease = str(uuid.uuid4())
    with conn.cursor() as cursor:
        # A durable lease works with Neon's transaction pool; session advisory
        # locks do not survive a change of pooled server connection.
        cursor.execute("""INSERT INTO app_cache (key, value)
            VALUES ('strava_sync_lock', %s::jsonb)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
            WHERE (app_cache.value->>'expires_at')::double precision < %s
            RETURNING key""", (json.dumps({"lease": lease, "expires_at": time.time()+1200}), time.time()))
        acquired = cursor.fetchone() is not None
    conn.commit()
    if not acquired:
        log.info("Strava sync already running")
        return
    try:
        credentials = read_credentials(conn)
        if not credentials or not credentials.get("refresh_token"):
            raise StravaReviewRequired("Authorize Strava at /strava first")
        session = authenticated_session(conn, credentials)
        cfg = load_config()
        hevy = HevyClient(api_key=cfg.get("hevy_api_key"))
        if workout_id:
            workout = hevy.get_workout(workout_id)
            if not workout:
                raise ValueError("Hevy workout not found")
            workouts = [workout]
        else:
            since = config.get("since")
            if not since:
                raise ValueError("Automatic Strava sync requires an explicit starting timestamp")
            workouts = fetch_workouts(hevy, since=since)
        garmin = None
        supported = set(json.loads((Path(__file__).parent / "data/strava_exercises.json").read_text())["exercises"])
        uploader = StravaUploader(session, store)
        for workout in workouts:
            wid = workout["id"]
            state = store.get_app_config(f"strava_upload_{wid}") or {}
            if state.get("activity_id"):
                continue
            try:
                if state.get("upload_id") and state.get("status") != "rejected":
                    uploader.upload(workout, {})  # Resume processing without another POST.
                    continue
                if state.get("status") in {"submitting", "uncertain"}:
                    raise StravaReviewRequired("Previous upload requires reconciliation")
                # Wait for the watch and Garmin sync before producing the sole Strava activity.
                source_id = store.get_garmin_id(wid)
                if not source_id:
                    log.info("Strava deferred: no matched Garmin activity for %s", wid)
                    continue
                if garmin is None:
                    garmin = get_client(cfg.get("garmin_email"), cfg.get("garmin_password", ""),
                                        cfg.get("garmin_token_dir", "~/.garminconnect"))
                activity_details = garmin.get_activity(int(source_id))
                summary = activity_details.get("summaryDTO") or activity_details
                recording = watch_window(workout, activity_details)
                samples = hr_for_sync(store, garmin, recording, cfg, source_activity_id=source_id)
                payload = build_strength_payload(recording, samples,
                    exercise_mappings(workout, config.get("exercise_types", {})),
                    timezone_name=config.get("timezone", "America/Toronto"),
                    calories=summary.get("calories"),
                    supported_exercises=supported)
                # Retain the exact, validated payload before modifying any original.
                previous_payload = store.get_app_config(f"strava_payload_{wid}")
                matches = matching_activities(session, recording)
                if len(matches) > 1:
                    raise StravaReviewRequired("Multiple matching activities; select the original manually")
                if replace_activity is not None:
                    if matches and int(matches[0]["id"]) != int(replace_activity):
                        raise StravaReviewRequired("The matched activity is not the explicitly authorized original")
                    if not matches:
                        backup = store.get_app_config(f"strava_original_{wid}") or {}
                        if int(backup.get("activity_id", 0)) != int(replace_activity):
                            raise StravaReviewRequired("No backup of the explicitly authorized original exists")
                        original = session.get(f"https://www.strava.com/api/v3/activities/{int(replace_activity)}", timeout=30)
                        if original.status_code != 404:
                            raise StravaReviewRequired("The authorized original is still present or could not be verified")
                if dry_run:
                    log.info("Validated %d sets and %d real HR samples; %d matching activities",
                             len(payload["sets"]), len(payload["streams"]["time"]), len(matches))
                    continue
                if state.get("status") == "rejected":
                    if not workout_id or not previous_payload or previous_payload == payload:
                        raise StravaReviewRequired("Rejected uploads require a manual test with a corrected payload")
                    response = session.get(f"https://www.strava.com/api/v3/uploads/{int(state['upload_id'])}", timeout=30)
                    response.raise_for_status()
                    rejected = response.json()
                    if not rejected.get("error") or rejected.get("activity_id"):
                        raise StravaReviewRequired("Previous rejection could not be confirmed; no retry")
                    store.set_app_config(f"strava_rejected_{wid}_{state['upload_id']}", {
                        "receipt": rejected, "payload": previous_payload})
                    store.set_app_config(f"strava_upload_{wid}", {"status": "retry_ready"})
                store.set_app_config(f"strava_payload_{wid}", payload)
                if matches:
                    prepare_replacement(session, store, workout, matches[0],
                        allowed_id=replace_activity, automatic=bool(config.get("replace_garmin_strength")))
                receipt = uploader.upload(workout, payload)
                for _ in range(5):
                    if receipt.get("status") != "processing":
                        break
                    time.sleep(2)
                    receipt = uploader.upload(workout, payload)
                log.info("Strava %s for Hevy %s", receipt["status"], wid)
            except StravaReviewRequired as exc:
                log.warning("Strava deferred for %s: %s", wid, exc)
                if workout_id:
                    raise
    finally:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM app_cache WHERE key='strava_sync_lock' AND value->>'lease'=%s", (lease,))
        conn.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workout-id")
    parser.add_argument("--replace-activity", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.replace_activity and not args.workout_id:
        parser.error("--replace-activity requires --workout-id")
    logging.basicConfig(level=logging.INFO)
    sync_destination(workout_id=args.workout_id, replace_activity=args.replace_activity, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
