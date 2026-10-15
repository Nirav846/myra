// Central API configuration for MYRA frontend.
// All fetch() calls must import from here — never hardcode the URL.

/**
 * Why the default is **same-origin** rather than `http://localhost:8000`
 * ---------------------------------------------------------------------
 * Phase 11 traced the Mission Control footer showing `transport error: Failed
 * to fetch` to this exact line. The absolute URL makes every panel request a
 * *cross-origin* request into the browser's loopback address space, and that
 * fails for any page origin the backend's CORS allowlist does not name. It was
 * hardcoded to just two origins (`localhost:3000`, `localhost:5173`), while the
 * dev server runs `--host=0.0.0.0` and therefore actively advertises LAN and
 * `127.0.0.1` origins. Reproduced in Chromium (backend healthy, 200 OK):
 *
 *   page origin                     absolute localhost:8000   relative /api
 *   http://localhost:3000           200                       200
 *   http://127.0.0.1:3000           "Failed to fetch"        200
 *   http://192.168.x.x:3000 (LAN)   "Failed to fetch"        200
 *
 * The relative default routes through the Vite `/api` proxy that is already
 * configured, so development needs no CORS grant at all and works from any
 * host. An absolute base is still supported for genuinely split deployments
 * (static frontend + separate API host) via `VITE_API_BASE`, which two views
 * already used independently.
 *
 * `VITE_API_BASE` is the *API prefix* (e.g. `http://localhost:8000/api`), not
 * the root, so set it to the same value `API_BASE` had before.
 */
const configuredBase = (import.meta.env?.VITE_API_BASE as string | undefined)?.trim();

/** True when an explicit absolute API host was configured. */
export const USING_ABSOLUTE_API = Boolean(configuredBase);

/**
 * API prefix every caller concatenates onto (`${API_BASE}/pipeline/readiness`).
 *
 * The default is the **relative** `/api`, not an empty string: callers append
 * paths *after* the prefix, so an empty base would silently produce
 * `/pipeline/readiness` and miss the router's `/api` mount entirely - which
 * Vite answers with the SPA fallback `index.html`, i.e. HTML where JSON is
 * expected. Same-origin + `/api` keeps the call on the page's own origin so the
 * browser never performs a cross-origin call into loopback, and the configured
 * Vite proxy (or a production reverse proxy) forwards it.
 */
export const API_BASE = configuredBase ?? '/api';

/**
 * Origin root, for the rare consumer that needs it without the `/api` prefix
 * (e.g. an absolute asset or SSE URL). Empty in same-origin mode, because
 * `location.origin` is the correct answer there.
 */
export const API_ROOT = configuredBase ? configuredBase.replace(/\/api\/?$/, '') : '';

export const MYRA_AUTH_HEADERS = { 'X-Myra-Auth': 'myra-local-dev-2026' };