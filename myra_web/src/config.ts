  // Central API configuration for MYRA frontend.
  // All fetch() calls must import from here — never hardcode the URL.
  export const API_ROOT = 'http://localhost:8000';
  export const API_BASE = `${API_ROOT}/api`;
  export const MYRA_AUTH_HEADERS = { 'X-Myra-Auth': 'myra-local-dev-2026' };