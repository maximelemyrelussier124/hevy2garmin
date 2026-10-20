import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { readStravaCredentials, sameOrigin } from "@/lib/strava-auth";

export const runtime = "nodejs";

export async function POST(request: Request) {
  if (!sameOrigin(request)) return new Response("Invalid origin", { status: 403 });
  const credentials = await readStravaCredentials();
  if (!credentials?.refresh_token) return new Response("Connect Strava first", { status: 400 });
  const data = await request.formData();
  if (data.get("replace_garmin_strength") !== "yes") return new Response("Replacement consent required", { status: 400 });
  const sql = getDb();
  const rows = await sql`SELECT value FROM app_cache WHERE key = 'strava_sync'`;
  const existing = rows[0]?.value ?? {};
  if (!existing.single_upload_route_ready) {
    return new Response("Configure a single upload route before enabling automatic publishing", { status: 409 });
  }
  const value = { ...existing, enabled: true, replace_garmin_strength: true,
    timezone: "America/Toronto", since: existing.since ?? new Date().toISOString() };
  await sql`INSERT INTO app_cache (key, value) VALUES ('strava_sync', ${sql.json(value)})
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`;
  return NextResponse.redirect(new URL("/strava?result=enabled", request.url), 303);
}
