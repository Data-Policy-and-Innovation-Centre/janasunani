// Client-side fetch layer for the Janasunani serving API. No auth, no SSR — the
// browser calls the API directly. Base URL is env-configurable.
import type { GrievanceResult, HistoryPage } from "@/lib/types";

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
