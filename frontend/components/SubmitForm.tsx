"use client";

import { useState } from "react";
import { submitGrievance } from "@/lib/api";
import type { GrievanceResult } from "@/lib/types";
import { ResultView } from "./ResultView";

export function SubmitForm() {
  const [text, setText] = useState("");
  const [district, setDistrict] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GrievanceResult | null>(null);

  const canSubmit = (!!text.trim() || !!file) && !loading;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const res = await submitGrievance({
        // API takes text XOR file — prefer the uploaded file if present.
        text: file ? undefined : text,
        file: file ?? undefined,
        district,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
    } finally {
      setLoading(false);
    }
  }

  function reset() {
    setText("");
    setDistrict("");
    setFile(null);
    setResult(null);
    setError(null);
  }

  return (
    <div className="flex flex-col gap-6">
      <form
        onSubmit={onSubmit}
        className="flex flex-col gap-4 rounded-md border border-hair bg-surface p-5"
      >
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="grievance-text"
            className="text-xs font-semibold uppercase tracking-wide text-maroon"
          >
            Grievance text
          </label>
          <textarea
            id="grievance-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={!!file}
            rows={6}
            placeholder="Describe the grievance in the citizen's own words…"
            className="resize-y rounded-sm border border-hair bg-surface px-3 py-2 text-sm text-text-body outline-none focus:border-maroon disabled:bg-card disabled:text-text-secondary"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="grievance-file"
            className="text-xs font-semibold uppercase tracking-wide text-maroon"
          >
            …or upload a document
          </label>
          <input
            id="grievance-file"
            type="file"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm text-text-body file:mr-3 file:rounded-sm file:border-0 file:bg-maroon file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-white hover:file:bg-maroon-full"
          />
          {file && (
            <span className="text-xs text-text-secondary">
              Selected: {file.name} — text input is disabled while a file is
              chosen.{" "}
              <button
                type="button"
                onClick={() => setFile(null)}
                className="font-semibold text-maroon underline"
              >
                clear
              </button>
            </span>
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="district"
            className="text-xs font-semibold uppercase tracking-wide text-maroon"
          >
            District (optional)
          </label>
          <input
            id="district"
            value={district}
            onChange={(e) => setDistrict(e.target.value)}
            placeholder="e.g. Khordha"
            className="rounded-sm border border-hair bg-surface px-3 py-2 text-sm text-text-body outline-none focus:border-maroon"
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-sm bg-maroon px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-maroon-full disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Processing…" : "Submit grievance"}
          </button>
          {(result || error) && (
            <button
              type="button"
              onClick={reset}
              className="rounded-sm border border-hair px-4 py-2 text-sm font-medium text-text-body transition-colors hover:border-maroon hover:text-maroon"
            >
              New grievance
            </button>
          )}
        </div>

        {error && (
          <p className="rounded-sm border border-negative/40 bg-negative/10 px-3 py-2 text-sm text-negative">
            {error}
          </p>
        )}
      </form>

      {result && <ResultView result={result} />}
    </div>
  );
}
