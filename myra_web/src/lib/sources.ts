/**
 * Data Sources panel API client + types.
 *
 * Backed by myra_web/routes/sources.py (GET/PATCH /api/sources,
 * POST /api/sources/{name}/reset and /test). Configuration is persisted in
 * the metadata sidecar by myra_app/data_sources/source_config.py.
 */
import { API_BASE } from '../config';

export interface SourceHealth {
  fail_streak: number;
  empty_streak: number;
  cooling_down: boolean;
  cooldown_until: string | null;
  cooldown_remaining_s: number;
  last_ok: string | null;
}

export interface DataSource {
  name: string;
  label: string;
  description: string;
  tier: 'primary' | 'fallback' | 'bulk';
  cost: 'fast' | 'medium' | 'slow';
  capabilities: string[];
  implemented: boolean;
  in_registry: boolean;
  disable_warning: string | null;
  enabled: boolean;
  rate_per_sec: number;
  ttl_s: number;
  timeout_s: number;
  priority: number;
  /** Live health snapshot. Optional: write endpoints always include it, but the
   *  UI must tolerate its absence rather than crash. */
  health?: SourceHealth;
}

export interface SourceListResponse {
  version: number;
  sources: DataSource[];
}

export type SourceTuning = Partial<
  Pick<DataSource, 'enabled' | 'rate_per_sec' | 'ttl_s' | 'timeout_s' | 'priority'>
>;

export interface SourceTestResult {
  source: string;
  supported: boolean;
  ok: boolean;
  symbol?: string;
  values?: Record<string, number | string>;
  detail?: string;
  error?: string;
  elapsed_s?: number;
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body && (body.detail as string | undefined)) ?? `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return body as T;
}

export async function fetchSources(): Promise<DataSource[]> {
  const res = await fetch(`${API_BASE}/sources`);
  const body = await jsonOrThrow<SourceListResponse>(res);
  return body.sources;
}

export async function patchSource(name: string, patch: SourceTuning): Promise<DataSource> {
  const res = await fetch(`${API_BASE}/sources/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return jsonOrThrow<DataSource>(res);
}

export async function resetSource(name: string): Promise<DataSource> {
  const res = await fetch(`${API_BASE}/sources/${encodeURIComponent(name)}/reset`, {
    method: 'POST',
  });
  return jsonOrThrow<DataSource>(res);
}

export async function testSource(name: string): Promise<SourceTestResult> {
  const res = await fetch(`${API_BASE}/sources/${encodeURIComponent(name)}/test`, {
    method: 'POST',
  });
  return jsonOrThrow<SourceTestResult>(res);
}
