import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { hasStravaScopes, readStravaCredentials, saveStravaCredentials } from "@/lib/strava-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const state = params.get("state") ?? "";
  const expected = request.cookies.get("h2g_strava_state")?.value ?? "";
  const finish = (result: string) => {
    const response = NextResponse.redirect(new URL(`/strava?result=${result}`, request.url));
    response.cookies.set("h2g_strava_state", "", { maxAge: 0, path: "/api/strava" });
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    return response;
  };
  if (!/^[a-f0-9]{64}$/.test(state) || expected.length !== state.length ||
      !timingSafeEqual(Buffer.from(state), Buffer.from(expected))) return finish("invalid-state");
  if (params.get("error")) return finish("cancelled");
  if (!params.get("code") || !hasStravaScopes(params.get("scope") ?? "")) return finish("missing-permissions");
  const credentials = await readStravaCredentials();
  if (!credentials) return finish("configure-first");
  try {
    const response = await fetch("https://www.strava.com/oauth/token", {
      method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ client_id: credentials.client_id,
        client_secret: credentials.client_secret, code: params.get("code")!,
        grant_type: "authorization_code" }),
      signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) return finish("token-failed");
    const token = await response.json();
    if (!token.refresh_token || !token.access_token || !token.athlete?.id) return finish("token-failed");
    await saveStravaCredentials({ ...credentials, access_token: token.access_token,
      refresh_token: token.refresh_token, expires_at: token.expires_at, athlete_id: token.athlete.id });
    return finish("connected");
  } catch {
    return finish("connection-failed");
  }
}
