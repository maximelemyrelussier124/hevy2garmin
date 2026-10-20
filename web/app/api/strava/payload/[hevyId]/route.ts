import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Protected by the ordinary dashboard session in proxy.ts. No credentials or
// tokens are returned; this is the exact prepared workout for troubleshooting.
export async function GET(_request: Request, context: { params: Promise<{ hevyId: string }> }) {
  const { hevyId } = await context.params;
  if (!/^[a-f0-9-]{36}$/i.test(hevyId)) return new Response("Invalid workout ID", { status: 400 });
  const sql = getDb();
  const rows = await sql`SELECT value FROM app_cache WHERE key = ${`strava_payload_${hevyId}`}`;
  if (!rows[0]) return new Response("No prepared workout", { status: 404 });
  return NextResponse.json(rows[0].value, { headers: { "Cache-Control": "no-store" } });
}
