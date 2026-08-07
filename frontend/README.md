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
  "mock result" badge. Its triage states are deterministic illustrations, not
  findings about real grievances.

Whichever backend is running, note that `routing.method` frequently comes
back `"fallback"` (low confidence) rather than `"rules"` — the real
category→department crosswalk is still being built out (see
[ROADMAP.md](../docs/ROADMAP.md)) and the router degrades to a generic public
grievance cell rather than failing. This is expected; the UI renders it with
an explanatory note, not as an error.

The result screen's triage banner is advisory only. Possible resubmissions link
to the existing history search, campaigns use a distinct collective-grievance
treatment with the related-filing count. Low-signal review currently exposes
an explicit, reason-coded abstention and non-content OCR-quality evidence; it
does not emit a score or a "clean" finding. Nothing in the banner
blocks or rejects a submission. Learned routes show their aggregate support
and destination concentration beside the existing confidence and escalation
chain.

## Structure

- `app/` — routes: `/` (submit a grievance), `/history` (browse/search), and
  `/supervisor` (aggregate-only Phase 15 briefing).
- `components/` — `SubmitForm`, `ResultView` (the five-step result cards),
  `TriageBanner`, `HistoryView`, `SupervisorDashboard`, `SupervisorView`, and
  `ui.tsx` (Card/Field/Badge primitives).
- `lib/api.ts` — the fetch client (`submitGrievance`, `fetchHistory`,
  `fetchSupervisorDashboard`).
- `lib/types.ts` — TypeScript mirror of `janasunani/serving/schemas.py`. Do
  not rename these fields without a matching backend contract change.
- `lib/supervisor.ts` — typed, aggregate-only supervisor response contract.
  The browser accepts only the backend's narrow DTO and rejects row-level
  fields. Until a validated artifact exists, each panel fails closed with its
  own requirement; it never turns a manual duplicate baseline into the dedup
  capability or a placeholder into a worked spike.

## Gate

```bash
npm test
npm run lint
npm run build
```

Both must pass before opening a PR.
