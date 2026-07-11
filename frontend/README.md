Next.js 16 + TypeScript + Tailwind v4 demo frontend for Janasunani, styled to
the DPIC brand (maroon accent, Calibri type). It talks to the serving API
defined by `janasunani/serving/schemas.py` — a frozen contract shared by both
the mock API and the real pipeline API, so the frontend needs no code changes
to point at either.

## Getting started

```bash
cp .env.local.example .env.local   # then edit NEXT_PUBLIC_API_URL if needed
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

To bring up the **live API and this frontend together**, run `make up` from the
repo root (throwaway Postgres + `janasunani-api-live` + `npm run dev`, wired to
each other; one `Ctrl-C` stops all, `make down` cleans up).

## Pointing at an API

Set `NEXT_PUBLIC_API_URL` in `.env.local` (see `.env.local.example`). It is
read client-side only — the browser calls the API directly, nothing routes
through Next.js SSR/API routes, and no citizen text ever leaves the browser
except to this one local endpoint.

Two backends implement the same contract:

- **Real pipeline** (OCR, Presidio PII redaction, MuRIL categorizer,
  routing) — `uv run --extra demo janasunani-api-live`. See
  [`../docs/DEMO.md`](../docs/DEMO.md) for prerequisites (DVC model pull,
  tesseract/poppler, throwaway Postgres or no `OLTP_DB_URL` for an in-memory
  store). First boot is slow (model warm-up); `/health` must report
  `{"processor":"pipeline"}`.
- **Mock** — `uv run --extra serving janasunani-api`. Canned/regex responses,
  useful for fast UI iteration without models loaded. Results from this API
  come back with `routing.method: "mock"` and the UI marks them with a
  "mock result" badge.

Whichever backend is running, note that `routing.method` frequently comes
back `"fallback"` (low confidence) rather than `"rules"` — the real
category→department crosswalk is still being built out (see
[ROADMAP.md](../docs/ROADMAP.md)) and the router degrades to a generic public
grievance cell rather than failing. This is expected; the UI renders it with
an explanatory note, not as an error.

## Structure

- `app/` — routes: `/` (submit a grievance) and `/history` (browse/search).
- `components/` — `SubmitForm`, `ResultView` (the five-step result cards),
  `HistoryView`, and `ui.tsx` (Card/Field/Badge primitives).
- `lib/api.ts` — the fetch client (`submitGrievance`, `fetchHistory`).
- `lib/types.ts` — TypeScript mirror of `janasunani/serving/schemas.py`. Do
  not rename these fields without a matching backend contract change.

## Gate

```bash
npm run lint
npm run build
```

Both must pass before opening a PR.
