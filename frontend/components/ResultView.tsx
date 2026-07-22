import type { ReactNode } from "react";
import type { GrievanceResult, PIIEntity } from "@/lib/types";
import { Badge, Card, Field } from "./ui";

/** Render extracted text with detected PII spans highlighted in place.
 *  Spans (entity/start/end) are over the extracted/unredacted text. */
function HighlightedText({
  text,
  entities,
}: {
  text: string;
  entities: PIIEntity[];
}) {
  const spans = [...entities]
    .filter((e) => e.start >= 0 && e.end <= text.length && e.start < e.end)
    .sort((a, b) => a.start - b.start);

  const out: ReactNode[] = [];
  let cursor = 0;
  spans.forEach((e, i) => {
    if (e.start < cursor) return; // skip overlaps defensively
    if (e.start > cursor) out.push(text.slice(cursor, e.start));
    out.push(
      <mark
        key={i}
        title={e.entity}
        className="rounded-sm bg-maroon/10 px-0.5 font-medium text-maroon"
      >
        {text.slice(e.start, e.end)}
      </mark>,
    );
    cursor = e.end;
  });
  if (cursor < text.length) out.push(text.slice(cursor));

  return (
    <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-text-body">
      {out}
    </p>
  );
}

function confidenceTone(c: number): "positive" | "neutral" | "negative" {
  if (c >= 0.7) return "positive";
  if (c >= 0.4) return "neutral";
  return "negative";
}

export function ResultView({ result }: { result: GrievanceResult }) {
  const { extraction, redaction, classification, routing } = result;
  const isMock = routing.method === "mock";
  const isFallbackRouting = routing.method === "fallback";

  return (
    <div className="flex flex-col gap-4">
      {/* Header strip */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-hair bg-card px-4 py-3">
        <div className="flex flex-col">
          <span className="text-xs font-semibold uppercase tracking-wide text-maroon">
            Ticket
          </span>
          <span className="font-mono text-sm font-bold text-text-dark">
            {result.ticket_no}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone="neutral">{result.status}</Badge>
          {isMock && <Badge tone="maroon">mock result</Badge>}
        </div>
      </div>

      {isMock && (
        <p className="rounded-md border border-hair bg-panel px-4 py-2 text-xs text-text-secondary">
          Illustrative only — produced by the mock processor. Redaction here is
          a toy regex, not the production PII analyzer.
        </p>
      )}

      {/* 1. Extraction */}
      <Card
        step={1}
        title="Extracted text"
        hint={
          extraction.source === "document"
            ? `${extraction.ocr_model ?? "ocr"} · ${extraction.pages ?? "?"} page(s)`
            : "direct text (no OCR)"
        }
      >
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-text-body">
          {extraction.extracted_text}
        </p>
      </Card>

      {/* 2. Redaction */}
      <Card
        step={2}
        title="PII detection & redaction"
        hint={`${redaction.entities.length} span(s)`}
      >
        <div className="flex flex-col gap-3">
          <div>
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-maroon">
              Redacted text
            </span>
            <p className="whitespace-pre-wrap break-words rounded-sm bg-panel p-2 text-sm leading-relaxed text-text-body">
              {redaction.redacted_text}
            </p>
          </div>
          {redaction.entities.length > 0 && (
            <div>
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-maroon">
                Detected spans (in original)
              </span>
              <div className="mb-2 flex flex-wrap gap-1.5">
                {redaction.entities.map((e, i) => (
                  <Badge key={i} tone="neutral">
                    {e.entity}
                  </Badge>
                ))}
              </div>
              <HighlightedText
                text={extraction.extracted_text}
                entities={redaction.entities}
              />
            </div>
          )}
        </div>
      </Card>

      {/* 3. Classification */}
      <Card step={3} title="Classification">
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Category" value={classification.category} />
          <Field label="Subcategory" value={classification.subcategory} />
          <Field
            label="Language"
            value={<span className="font-mono">{classification.language}</span>}
          />
        </dl>
      </Card>

      {/* 4. Summary */}
      <Card step={4} title="Summary">
        <p className="text-sm leading-relaxed text-text-body">
          {result.summary}
        </p>
      </Card>

      {/* 5. Routing */}
      <Card
        step={5}
        title="Routing"
        hint={`method: ${routing.method}`}
      >
        {isFallbackRouting && (
          <p className="mb-3 rounded-sm bg-panel px-3 py-2 text-xs text-text-secondary">
            No specific rule matched this category/district — routed to the
            generic public grievance cell as a safety net. Confidence is low
            by design.
          </p>
        )}
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Department" value={routing.dept} />
          <Field label="Handling office" value={routing.office} />
          <Field label="Designation" value={routing.designation} />
          <Field
            label="Escalation authority"
            value={routing.escalation_authority}
          />
          <Field
            label="Confidence"
            value={
              <Badge tone={confidenceTone(routing.confidence)}>
                {(routing.confidence * 100).toFixed(0)}%
              </Badge>
            }
          />
          <Field
            label="Method"
            value={<Badge tone="neutral">{routing.method}</Badge>}
          />
        </dl>
      </Card>
    </div>
  );
}
