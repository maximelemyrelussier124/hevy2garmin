import { randomBytes } from "node:crypto";
import { NextResponse } from "next/server";
import { readStravaCredentials } from "@/lib/strava-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const credentials = await readStravaCredentials();
  if (!credentials?.client_id) return NextResponse.redirect(new URL("/strava", request.url));
  const state = randomBytes(32).toString("hex");
  const url = new URL("https://www.strava.com/oauth/authorize");
  url.search = new URLSearchParams({
    client_id: credentials.client_id,
    redirect_uri: new URL("/api/strava/callback", request.url).href,
    response_type: "code", approval_prompt: "force",
    scope: "activity:write,activity:read_all", state,
  }).toString();
  const response = NextResponse.redirect(url);
  response.cookies.set("h2g_strava_state", state, {
    httpOnly: true, secure: new URL(request.url).protocol === "https:",
    sameSite: "lax", maxAge: 600, path: "/api/strava",
  });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
