import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const credentials = vi.hoisted(() => ({
  read: vi.fn(async () => ({ client_id: "123", client_secret: "test-secret" })),
  save: vi.fn(),
}));
vi.mock("@/lib/strava-auth", () => ({
  readStravaCredentials: credentials.read,
  saveStravaCredentials: credentials.save,
  hasStravaScopes: (scope: string) => scope.split(",").includes("activity:write") && scope.split(",").includes("activity:read_all"),
}));
import { GET } from "./route";

afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); });

describe("Strava callback state", () => {
  it("rejects missing state cookies before accessing credentials", async () => {
    const response = await GET(new NextRequest("https://hevy.example/api/strava/callback?state=" + "a".repeat(64)));
    expect(response.headers.get("location")).toContain("invalid-state");
    expect(credentials.read).not.toHaveBeenCalled();
  });
  it("completes a valid OAuth return without a dashboard session cookie", async () => {
    const state = "a".repeat(64);
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ access_token: "access", refresh_token: "refresh", expires_at: 9999999999, athlete: { id: 7 } })));
    const response = await GET(new NextRequest(`https://hevy.example/api/strava/callback?state=${state}&code=test-code&scope=read,activity:write,activity:read_all`, {
      headers: { cookie: `h2g_strava_state=${state}` },
    }));
    expect(response.headers.get("location")).toBe("https://hevy.example/strava?result=connected");
    expect(credentials.save).toHaveBeenCalledWith(expect.objectContaining({ athlete_id: 7, refresh_token: "refresh" }));
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
  });
});
