"""Forward original non-strength Garmin recordings after native sync is disabled."""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime, timezone

from hevy2garmin.strava import StravaReviewRequired, StravaUploader

log = logging.getLogger("hevy2garmin.strava")


def original_fit(download):
    if not isinstance(download, bytes) or not download:
        raise StravaReviewRequired("Original Garmin file could not be downloaded")
    stream = io.BytesIO(download)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            files = [item for item in archive.infolist() if item.filename.lower().endswith(".fit")]
            if len(files) != 1 or files[0].file_size > 100_000_000:
                raise StravaReviewRequired("Garmin archive must contain exactly one reasonable FIT file")
            download = archive.read(files[0])
    if len(download) < 14 or download[8:12] != b".FIT":
        raise StravaReviewRequired("Garmin download is not an original FIT activity")
    return download


def gmt(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result if result.utcoffset() is not None else result.replace(tzinfo=timezone.utc)


def mirror_garmin(garmin, session, store, config, reserved_ids):
    from garminconnect import Garmin

    since = gmt(config["since"])
    activities = garmin.get_activities_by_date(since.date().isoformat(), datetime.now(timezone.utc).date().isoformat())
    uploader = StravaUploader(session, store)
    for activity in activities or []:
        source_id = str(activity["activityId"])
        if source_id in reserved_ids:
            continue
        start = gmt(activity.get("startTimeGMT") or "")
        if start < since:
            continue
        kind = str((activity.get("activityType") or {}).get("typeKey", ""))
        if "strength" in kind:
            log.info("Garmin strength recording %s waits for its Hevy workout", source_id)
            continue
        key = f"strava_upload_garmin-{source_id}"
        state = store.get_app_config(key) or {}
        if state.get("activity_id"):
            continue
        workout = {"id": f"garmin-{source_id}", "title": activity.get("activityName", "Garmin activity")}
        if state.get("upload_id"):
            uploader.upload(workout, {})
            continue
        if state.get("status") in {"submitting", "uncertain"}:
            raise StravaReviewRequired(f"Garmin recording {source_id} has an uncertain Strava upload")
        response = session.get("https://www.strava.com/api/v3/athlete/activities", params={
            "after": int(start.timestamp()-11), "before": int(start.timestamp()+11), "per_page": 200,
        }, timeout=30)
        response.raise_for_status()
        duration = float(activity.get("elapsedDuration") or activity.get("duration") or 0)
        existing = [item for item in response.json()
                    if abs(gmt(item["start_date"]).timestamp()-start.timestamp()) <= 10
                    and abs(item.get("elapsed_time", 0)-duration) <= max(30, duration*0.05)]
        if len(existing) == 1:
            store.set_app_config(key, {"status": "already_present", "activity_id": existing[0]["id"]})
            continue
        if existing:
            raise StravaReviewRequired("Multiple Strava recordings match the Garmin activity")
        content = original_fit(garmin.download_activity(source_id, Garmin.ActivityDownloadFormat.ORIGINAL))
        # Persist the source reference; the original FIT remains in Garmin.
        store.set_app_config(f"strava_garmin_source_{source_id}", {"garmin_activity_id": source_id})
        receipt = uploader.upload(workout, {}, data_type="fit", file_content=content)
        log.info("Original Garmin recording %s: Strava %s", source_id, receipt["status"])
