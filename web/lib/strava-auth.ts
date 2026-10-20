import { getDb } from "./db";

export type StravaCredentials = {
  client_id: string;
  client_secret: string;
  access_token?: string;
  refresh_token?: string;
  expires_at?: number;
  athlete_id?: number;
};

export async function readStravaCredentials(): Promise<StravaCredentials | null> {
  const sql = getDb();
  const rows = await sql`SELECT credentials FROM platform_credentials WHERE platform = 'strava' LIMIT 1`;
  return rows[0]?.credentials ?? null;
}

export async function saveStravaCredentials(credentials: StravaCredentials) {
  const sql = getDb();
  const status = credentials.refresh_token ? "active" : "disconnected";
  await sql`INSERT INTO platform_credentials (platform, auth_type, credentials, status, connected_at)
    VALUES ('strava', 'oauth', ${sql.json(credentials)}, ${status}, NOW())
    ON CONFLICT (platform) DO UPDATE SET credentials = EXCLUDED.credentials,
    auth_type = 'oauth', status = EXCLUDED.status, connected_at = NOW()`;
}

export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  return origin === new URL(request.url).origin;
}

export function hasStravaScopes(scope: string): boolean {
  const scopes = new Set(scope.split(","));
  return scopes.has("activity:write") && scopes.has("activity:read_all");
}
