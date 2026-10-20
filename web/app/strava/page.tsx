import { readStravaCredentials } from "@/lib/strava-auth";
import { getDb } from "@/lib/db";

export const dynamic = "force-dynamic";

export default async function StravaPage({ searchParams }: {
  searchParams: Promise<{ result?: string }>;
}) {
  const credentials = await readStravaCredentials();
  const { result } = await searchParams;
  const connected = Boolean(credentials?.refresh_token);
  const sql = getDb();
  const rows = await sql`SELECT value FROM app_cache WHERE key = 'strava_sync'`;
  const enabled = Boolean(rows[0]?.value?.enabled);
  return <main className="mx-auto max-w-xl p-6 space-y-6">
    <h1 className="text-2xl font-bold">Strava</h1>
    <p>Sync Hevy exercises, sets, repetitions and weights with Garmin heart rate in one Weight Training activity.</p>
    <p>{connected ? (enabled ? "Strava connected. Automatic publishing is enabled." : "Strava connected. Automatic publishing is not enabled yet.") : "Connect your personal Strava application."}</p>
    {result && !["connected", "enabled"].includes(result) && <p role="alert">Connection incomplete ({result}). Try again.</p>}
    <form action="/api/strava/configure" method="post" className="space-y-4">
      <label className="block">Client ID
        <input className="block w-full border rounded p-2" name="client_id" required inputMode="numeric" defaultValue={credentials?.client_id ?? ""} />
      </label>
      <label className="block">Client Secret
        <input className="block w-full border rounded p-2" name="client_secret" type="password" required autoComplete="off" />
      </label>
      <p>Copy these from your Strava API application settings. They stay on your server and are never shown here again.</p>
      <button className="rounded border px-4 py-2" type="submit">Save and connect Strava</button>
    </form>
    {credentials?.client_id && <a className="underline" href="/api/strava/connect">Authorize Strava</a>}
    {connected && !enabled && <p>Automatic publishing requires a single upload route. The Strava API cannot replace existing activities. Matching activities are held for review instead of creating duplicates.</p>}
  </main>;
}
