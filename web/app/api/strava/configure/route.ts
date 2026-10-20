import { NextResponse } from "next/server";
import { readStravaCredentials, saveStravaCredentials, sameOrigin } from "@/lib/strava-auth";

export const runtime = "nodejs";

export async function POST(request: Request) {
  if (!sameOrigin(request)) return new Response("Invalid origin", { status: 403 });
  const data = await request.formData();
  const client_id = String(data.get("client_id") ?? "").trim();
  const client_secret = String(data.get("client_secret") ?? "").trim();
  if (!/^\d+$/.test(client_id) || !/^[a-zA-Z0-9]{20,128}$/.test(client_secret)) {
    return new Response("Invalid application credentials", { status: 400 });
  }
  const existing = await readStravaCredentials();
  await saveStravaCredentials({
    ...(existing?.client_id === client_id ? existing : {}), client_id, client_secret,
  });
  return NextResponse.redirect(new URL("/api/strava/connect", request.url), 303);
}
