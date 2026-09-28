// Client-side fetch layer for the Janasunani serving API. No auth, no SSR — the
// browser calls the API directly. Base URL is env-configurable.
import type { GrievanceResult, HealthResponse, HistoryPage } from "@/lib/types";
import type {
  BucketId,
  DashboardMeta,
  DisposalBreakdown,
  LiveSummary,
  Order,
  QueueFilters,
  QueuePage,
  RouteBreakdown,
  Timeline,
} from "@/lib/dashboard";

const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

/** Pull a human-readable message out of a failed response (FastAPI -> {detail}). */
async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail) && body.detail[0]?.msg) {
      return body.detail[0].msg;
    }
  } catch {
    /* non-JSON error body */
  }
  return `Request failed (${res.status})`;
}

/** POST /grievance — multipart. The API takes text XOR file (+ optional district). */
export async function submitGrievance(input: {
  text?: string;
  file?: File;
  district?: string;
}): Promise<GrievanceResult> {
  const form = new FormData();
  if (input.file) {
    form.append("file", input.file);
  } else if (input.text !== undefined && input.text.trim() !== "") {
    form.append("text", input.text);
  }
  if (input.district && input.district.trim() !== "") {
    form.append("district", input.district.trim());
  }

  const res = await fetch(`${API_BASE}/grievance`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  return (await res.json()) as GrievanceResult;
}

/** GET /health — which processor the API is running.
 *
 * `processor` is `"mock"` for the default `janasunani-api` skeleton and the
 * real processor's name for `janasunani-api-live`. The history table uses it
 * to mark rows that came from `MockHistory`, whose fabricated grievances are
 * otherwise indistinguishable from real ones in the columns shown.
 */
export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(await errorMessage(res));
  return (await res.json()) as HealthResponse;
}

/** GET /history — browse/search historical complaints from the lake. */
export async function fetchHistory(params: {
  q?: string;
  district?: string;
  category?: string;
  limit: number;
  offset: number;
}): Promise<HistoryPage> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.district) qs.set("district", params.district);
  if (params.category) qs.set("category", params.category);
  qs.set("limit", String(params.limit));
  qs.set("offset", String(params.offset));

  const res = await fetch(`${API_BASE}/history?${qs.toString()}`);
  if (!res.ok) throw new Error(await errorMessage(res));
  return (await res.json()) as HistoryPage;
}

/** GET a dashboard endpoint; unset params are dropped, never sent empty. */
async function dashboardGet<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") qs.set(key, String(value));
  }
  const query = qs.toString();
  const res = await fetch(`${API_BASE}/dashboard${path}${query ? `?${query}` : ""}`, { cache: "no-store" });
  if (!res.ok) throw new Error(await errorMessage(res));
  return (await res.json()) as T;
}

export const fetchDashboardMeta = () => dashboardGet<DashboardMeta>("/meta");

export const fetchLive = (office?: string, year?: string) => dashboardGet<LiveSummary>("/live", { office, year });

export const fetchQueue = (bucket: BucketId, office: string | undefined, year: string | undefined, filters: QueueFilters, offset = 0, limit = 50) =>
  dashboardGet<QueuePage>("/live/queue", { bucket, office, year, offset, limit, ...filters });

export const fetchTimeline = (ticketNo: string) =>
  dashboardGet<Timeline>(`/ticket/${encodeURIComponent(ticketNo)}/timeline`);

export const fetchDisposed = (params: { level: "overall" | "dept" | "category"; order?: Order; office?: string; year?: string; dept?: string }) =>
  dashboardGet<DisposalBreakdown>("/disposed", params);

export const fetchRoutes = (params: { office?: string; year?: string; dept?: string; category?: string; order?: Order }) =>
  dashboardGet<RouteBreakdown>("/disposed/routes", params);
