import type { ReactNode } from "react";

/** A staged result card with a maroon rule and title. */
export function Card({
  step,
  title,
  hint,
  children,
}: {
  step?: number;
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-md border border-hair bg-surface">
      <div className="flex items-baseline justify-between border-b border-hair bg-card px-4 py-2.5">
        <h2 className="flex items-baseline gap-2 text-sm font-bold text-text-dark">
          {step !== undefined && (
            <span className="text-maroon">{step}.</span>
          )}
          {title}
        </h2>
        {hint && (
          <span className="text-xs text-text-secondary">{hint}</span>
        )}
      </div>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

/** A small labelled key/value pair. */
export function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs font-semibold uppercase tracking-wide text-maroon">
        {label}
      </dt>
      <dd className="text-sm text-text-body">
        {value ?? <span className="text-text-secondary">—</span>}
      </dd>
    </div>
  );
}

type BadgeTone = "maroon" | "neutral" | "positive" | "negative";

const badgeTones: Record<BadgeTone, string> = {
  maroon: "bg-maroon text-white",
  neutral: "bg-card text-text-body border border-hair",
  positive: "bg-positive text-white",
  negative: "bg-negative text-white",
};

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: BadgeTone;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-sm px-2 py-0.5 text-xs font-semibold ${badgeTones[tone]}`}
    >
      {children}
    </span>
  );
}
