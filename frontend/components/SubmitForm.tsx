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
    <div className="flex flex-col gap-10">
      <form
        onSubmit={onSubmit}
        className="flex max-w-[820px] flex-col gap-7 border-t-2 border-maroon bg-surface p-7"
      >
        <div className="flex flex-col gap-2">
          <label
            htmlFor="grievance-text"
            className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-maroon-soft"
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
            className="resize-y rounded-none border-0 border-b border-hair bg-transparent px-0 py-2 text-[15px] leading-relaxed text-text-body outline-none transition-colors focus:border-maroon disabled:text-text-secondary"
          />
        </div>

        <div className="flex flex-col gap-2">
          <label
            htmlFor="grievance-file"
            className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-maroon-soft"
          >
            …or upload a document
          </label>
          <input
            id="grievance-file"
            type="file"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-[13px] text-text-secondary file:mr-3 file:rounded-full file:border-0 file:bg-maroon file:px-4 file:py-1.5 file:font-mono file:text-[10px] file:uppercase file:tracking-[0.12em] file:text-white hover:file:bg-maroon-full"
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

        <div className="flex flex-col gap-2">
          <label
            htmlFor="district"
            className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-maroon-soft"
          >
            District (optional)
          </label>
          <input
            id="district"
            value={district}
            onChange={(e) => setDistrict(e.target.value)}
            placeholder="e.g. Khordha"
            className="rounded-none border-0 border-b border-hair bg-transparent px-0 py-2 text-[15px] text-text-body outline-none transition-colors focus:border-maroon"
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-full bg-maroon px-6 py-2.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-white transition-colors duration-200 hover:bg-maroon-full disabled:cursor-not-allowed disabled:opacity-40"
          >
            {loading ? "Processing…" : "Submit grievance"}
          </button>
          {(result || error) && (
            <button
              type="button"
              onClick={reset}
              className="rounded-full border border-hair px-6 py-2.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-text-secondary transition-colors duration-200 hover:border-maroon hover:text-maroon"
            >
              New grievance
            </button>
          )}
        </div>

        {error && (
          <p className="border-l-2 border-negative bg-negative/5 py-2 pl-3 text-[13.5px] text-negative">
            {error}
          </p>
        )}
      </form>

      {result && <ResultView result={result} />}
    </div>
  );
}
