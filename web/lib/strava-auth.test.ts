import { describe, expect, it, vi } from "vitest";
vi.mock("./db", () => ({ getDb: vi.fn() }));
import { hasStravaScopes, sameOrigin } from "./strava-auth";

describe("Strava OAuth boundaries", () => {
  it("requires explicit write and read-all permissions", () => {
    expect(hasStravaScopes("read,activity:write,activity:read_all")).toBe(true);
    expect(hasStravaScopes("read,activity:write")).toBe(false);
    expect(hasStravaScopes("read,activity:read_all")).toBe(false);
  });
  it("rejects cross-site configuration posts", () => {
    const request = (origin?: string) => new Request("https://hevy.example/api/strava/configure", {
      method: "POST", headers: origin ? { origin } : {},
    });
    expect(sameOrigin(request("https://hevy.example"))).toBe(true);
    expect(sameOrigin(request("https://evil.example"))).toBe(false);
    expect(sameOrigin(request())).toBe(false);
  });
});
