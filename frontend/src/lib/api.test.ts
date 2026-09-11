import { afterEach, describe, expect, it, vi } from "vitest";
import { listScans, resolveApiKey, streamUrl } from "./api";

// D36: the key reaches a deployed browser at runtime through /config.js, which
// sets window.__ACRA_CONFIG__. VITE_API_KEY is only a local-development
// fallback. Both are reset after every test so no case leaks into another.
afterEach(() => {
  delete window.__ACRA_CONFIG__;
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("resolveApiKey: runtime config before build-time env", () => {
  it("prefers the runtime key served by /config.js", () => {
    vi.stubEnv("VITE_API_KEY", "build-time-key");
    window.__ACRA_CONFIG__ = { apiKey: "runtime-key" };
    expect(resolveApiKey()).toBe("runtime-key");
  });

  it("falls back to VITE_API_KEY when the runtime key is empty", () => {
    // The static public/config.js that vite dev, vite preview and Playwright
    // serve sets no key; local development must keep working from the env.
    vi.stubEnv("VITE_API_KEY", "build-time-key");
    window.__ACRA_CONFIG__ = { apiKey: "" };
    expect(resolveApiKey()).toBe("build-time-key");
  });

  it("falls back to VITE_API_KEY when /config.js never loaded", () => {
    vi.stubEnv("VITE_API_KEY", "build-time-key");
    expect(resolveApiKey()).toBe("build-time-key");
  });

  it("returns an empty string when neither source has a key", () => {
    vi.stubEnv("VITE_API_KEY", "");
    expect(resolveApiKey()).toBe("");
  });

  it("reads at call time, so a key set after import is still used", () => {
    vi.stubEnv("VITE_API_KEY", "");
    expect(resolveApiKey()).toBe("");
    window.__ACRA_CONFIG__ = { apiKey: "late-key" };
    expect(resolveApiKey()).toBe("late-key");
  });
});

describe("requests carry the resolved key", () => {
  function stubFetch() {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("sends the runtime key as X-API-Key", async () => {
    const fetchMock = stubFetch();
    window.__ACRA_CONFIG__ = { apiKey: "runtime-key" };
    await listScans();
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-API-Key"]).toBe("runtime-key");
  });

  it("sends no X-API-Key header when there is no key", async () => {
    const fetchMock = stubFetch();
    vi.stubEnv("VITE_API_KEY", "");
    await listScans();
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers).not.toHaveProperty("X-API-Key");
  });

  it("puts the runtime key on the stream URL, URL-encoded", () => {
    // EventSource cannot set headers, so the stream is the one place the key
    // travels in the query string (api_guard.is_stream_path).
    window.__ACRA_CONFIG__ = { apiKey: 'a"b\\c<d&e' };
    const url = new URL(streamUrl("scan-1"), "http://localhost");
    expect(url.searchParams.get("api_key")).toBe('a"b\\c<d&e');
  });

  it("leaves the stream URL bare when there is no key", () => {
    vi.stubEnv("VITE_API_KEY", "");
    expect(streamUrl("scan-1")).not.toContain("api_key");
  });
});
