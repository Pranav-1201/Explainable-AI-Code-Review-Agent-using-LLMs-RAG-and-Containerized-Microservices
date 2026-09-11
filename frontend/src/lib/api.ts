// Backend origin. Hardcoding localhost meant a deployed build could only ever
// talk to the developer's own machine; VITE_API_BASE is read at BUILD time
// (Vite inlines it), so the same source ships to dev and prod.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "http://localhost:8000")
  .replace(/\/+$/, "");

// Sent as X-API-Key when the backend has API_KEY configured.
//
// CAVEAT, deliberately accepted: anything inlined into a static bundle is
// readable by anyone who loads the page. This key raises the cost of drive-by
// abuse of the expensive /scan route (which clones repositories); it is NOT a
// per-user secret. Real multi-user auth needs a login + short-lived token flow
// — roadmap item G. Leave VITE_API_KEY unset for local dev, where the backend
// also leaves API_KEY unset and the API is open.
//
// Resolved at CALL time, from two sources in order (D36):
//   1. window.__ACRA_CONFIG__.apiKey, set by /config.js. A deployed web
//      container renders that file from its own API_KEY on each request
//      (config.js.template + Caddy templates). This is how a published image,
//      which is public and so can never carry a key, still sends one.
//   2. import.meta.env.VITE_API_KEY, inlined at build time. Local development
//      only: the static default public/config.js sets no key.
// Call time rather than module load mirrors api_guard's env-at-call-time rule,
// and is what lets tests change the key without re-importing this module.
export function resolveApiKey(): string {
  const runtime = window.__ACRA_CONFIG__?.apiKey;
  if (typeof runtime === "string" && runtime !== "") return runtime;
  return import.meta.env.VITE_API_KEY ?? "";
}

const SCAN_TIMEOUT_MS = 5 * 60 * 1000; // total scan deadline
const POLL_REQUEST_TIMEOUT_MS = 15_000;  // per-request timeout (15s)

// Every request goes through this so a newly added call cannot forget the key.
function apiHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...(extra ?? {}) };
  const key = resolveApiKey();
  if (key) headers["X-API-Key"] = key;
  return headers;
}

// EventSource cannot set headers, so the stream URL carries the key in the
// query string — the one place the backend accepts it (api_guard.is_stream_path).
// Exported for the tests in api.test.ts; not part of the page-facing API.
export function streamUrl(scanId: string): string {
  const base = `${API_BASE}/scan/${encodeURIComponent(scanId)}/stream`;
  const key = resolveApiKey();
  return key ? `${base}?api_key=${encodeURIComponent(key)}` : base;
}

// Helper: extract a readable error from a failed response
async function extractErrorMessage(response: Response): Promise<string> {
  if (response.status === 401) {
    return "Unauthorized — this page sent no valid API key. On a server, API_KEY must reach the web container (DEPLOYMENT.md).";
  }
  if (response.status === 429) {
    const retry = response.headers.get("Retry-After");
    return `Rate limited — too many requests${retry ? `, retry in ${retry}s` : ""}.`;
  }
  try {
    const body = await response.json();
    return body?.detail || body?.message || body?.error || `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status} ${response.statusText}`;
  }
}

// Start Scan
export async function startScan(repoUrl: string): Promise<string> {
  let response: Response;

  try {
    response = await fetch(`${API_BASE}/scan`, {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ repo_path: repoUrl }),
    });
  } catch (networkErr) {
    // Server not reachable at all
    throw new Error(
      `Cannot reach backend at ${API_BASE}. Is it running? (${(networkErr as Error).message})`
    );
  }

  if (!response.ok) {
    const reason = await extractErrorMessage(response);
    throw new Error(`Failed to start scan: ${reason}`);
  }

  const data = await response.json();

  if (!data?.scan_id) {
    throw new Error("Backend did not return a scan_id. Check your /scan endpoint response shape.");
  }

  return data.scan_id;
}

// Poll Scan Progress (with per-request timeout)
export async function getScanStatus(scanId: string): Promise<any> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), POLL_REQUEST_TIMEOUT_MS);

  let response: Response;

  try {
    response = await fetch(`${API_BASE}/scan/${encodeURIComponent(scanId)}`, {
      headers: apiHeaders(),
      signal: controller.signal,
    });
  } catch (err: any) {
    if (err?.name === "AbortError") {
      throw new Error(`Poll request timed out after ${POLL_REQUEST_TIMEOUT_MS / 1000}s`);
    }
    throw new Error(`Network error while polling: ${err.message}`);
  } finally {
    clearTimeout(timeoutId);
  }

  if (!response.ok) {
    const reason = await extractErrorMessage(response);
    throw new Error(`Failed to get scan status: ${reason}`);
  }

  return response.json();
}

// Poll a scan to completion against a total deadline. Shared by the SSE
// fallback and the legacy startAndPollScan wrapper.
async function pollScanResult(
  scanId: string,
  onProgress?: (status: any) => void,
  deadline: number = Date.now() + SCAN_TIMEOUT_MS
) {
  while (true) {
    if (Date.now() > deadline) {
      throw new Error(`Scan timed out after ${SCAN_TIMEOUT_MS / 60_000} minutes`);
    }

    const status = await getScanStatus(scanId);

    if (onProgress) onProgress(status);

    if (status.status === "complete") return status.result;

    if (status.status === "error") {
      throw new Error(`Scan failed on backend: ${status.error ?? "unknown reason"}`);
    }

    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

// Start Scan AND Wait Until Complete via polling. Kept for backward
// compatibility and used as the SSE fallback path below.
export async function startAndPollScan(
  repoPath: string,
  onProgress?: (status: any) => void
) {
  const scanId = await startScan(repoPath);
  return pollScanResult(scanId, onProgress);
}

// Subscribe to a scan over Server-Sent Events (Item E). One long-lived
// connection replaces 2s polling. On ANY failure — no EventSource in the
// runtime, a proxy that strips the stream, or a backend without the endpoint
// (404) — we transparently fall back to polling the same scan_id, so behaviour
// is never worse than before. Each SSE frame is the same dict shape that
// getScanStatus returns, so downstream handling is identical.
function streamScanResult(
  scanId: string,
  onProgress?: (status: any) => void
): Promise<any> {
  const deadline = Date.now() + SCAN_TIMEOUT_MS;

  // No EventSource (very old runtime / SSR) → poll.
  if (typeof EventSource === "undefined") {
    return pollScanResult(scanId, onProgress, deadline);
  }

  return new Promise((resolve, reject) => {
    const es = new EventSource(streamUrl(scanId));
    let settled = false;

    const deadlineTimer = setTimeout(() => {
      if (settled) return;
      settled = true;
      es.close();
      reject(new Error(`Scan timed out after ${SCAN_TIMEOUT_MS / 60_000} minutes`));
    }, SCAN_TIMEOUT_MS);

    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(deadlineTimer);
      es.close();
      fn();
    };

    es.onmessage = (evt: MessageEvent) => {
      let status: any;
      try {
        status = JSON.parse(evt.data);
      } catch {
        return; // ignore an unparsable frame
      }

      if (onProgress) onProgress(status);

      if (status.status === "complete") {
        settle(() => resolve(status.result));
      } else if (status.status === "error") {
        settle(() =>
          reject(new Error(`Scan failed on backend: ${status.error ?? "unknown reason"}`))
        );
      }
    };

    es.onerror = () => {
      // Connection error before a terminal frame (endpoint missing, proxy
      // dropped the stream, transient network). EventSource would auto-retry,
      // but we close it and fall back to polling from the same scan_id —
      // idempotent, it just reads state. A benign post-close error that fires
      // after a terminal frame is ignored because `settled` is already true.
      if (settled) return;
      settled = true;
      clearTimeout(deadlineTimer);
      es.close();
      pollScanResult(scanId, onProgress, deadline).then(resolve, reject);
    };
  });
}

// Start Scan AND stream progress to completion (SSE, with polling fallback).
export async function startAndStreamScan(
  repoPath: string,
  onProgress?: (status: any) => void
) {
  const scanId = await startScan(repoPath);
  return streamScanResult(scanId, onProgress);
}

// List past completed scans (persisted history — survives a page reload).
// Returns the raw backend rows: { id, repo, timestamp, health_score,
// files_analyzed, issues_found }. Callers map these to ScanHistoryItem.
export async function listScans(): Promise<any[]> {
  const response = await fetch(`${API_BASE}/scans`, { headers: apiHeaders() });

  if (!response.ok) {
    throw new Error(`Failed to list scans: ${await extractErrorMessage(response)}`);
  }

  const data = await response.json();
  return Array.isArray(data) ? data : (data.scans || []);
}

// Settings. These lived as raw fetch("http://localhost:8000/...") calls inside
// Settings.tsx, which meant they were invisible to the API_BASE / API key
// wiring and 401'd the moment auth was switched on. Routed through here so the
// trust boundary has exactly one crossing point on the client side too.
export async function getSettings(): Promise<any> {
  const response = await fetch(`${API_BASE}/settings`, { headers: apiHeaders() });
  if (!response.ok) {
    throw new Error(`Failed to fetch settings: ${await extractErrorMessage(response)}`);
  }
  return response.json();
}

export async function saveSettings(settings: any): Promise<any> {
  const response = await fetch(`${API_BASE}/settings`, {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(settings),
  });
  if (!response.ok) {
    throw new Error(`Failed to save settings: ${await extractErrorMessage(response)}`);
  }
  return response.json();
}

export async function resetSettings(): Promise<any> {
  const response = await fetch(`${API_BASE}/settings/reset`, {
    method: "POST",
    headers: apiHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Failed to reset settings: ${await extractErrorMessage(response)}`);
  }
  return response.json();
}