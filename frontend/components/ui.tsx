import type { ReactNode } from "react";
import { CountUp, Reveal } from "./motion";

/**
 * The page masthead: mono kicker, display-serif title, lead paragraph.
 *
 * `title` takes a node so a phrase can be wrapped in `<em>` — the `.page-title`
 * rule renders that phrase in maroon italic, which is the house title idiom.
 */
export function PageHead({
  kicker,
  title,
  lead,
  aside,
}: {
  kicker: string;
  title: ReactNode;
  lead?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className="pt-14 pb-10">
      <Reveal>
        <p className="kicker">{kicker}</p>
      </Reveal>
      <Reveal delay={90}>
        <h1 className="page-title mt-4">{title}</h1>
      </Reveal>
      {lead && (
        <Reveal delay={180}>
          <p className="mt-5 max-w-[680px] text-[15.5px] leading-[1.7] text-text-secondary">
            {lead}
          </p>
        </Reveal>
      )}
      {aside && <Reveal delay={260}>{aside}</Reveal>}
    </header>
  );
}

/** A small uppercase mono label. */
export function Kicker({ children }: { children: ReactNode }) {
  return <p className="kicker">{children}</p>;
}

/**
 * A staged section, ruled rather than boxed: a hairline above, a mono step
 * number in the left margin, and the title in display serif.
 */
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
    <Reveal as="section" className="border-t border-hair pt-5">
      <div className="mb-4 flex items-baseline justify-between gap-4">
        <h2 className="flex items-baseline gap-3">
          {step !== undefined && (
            <span className="font-mono text-[10px] tracking-[0.14em] text-maroon-soft">
              {String(step).padStart(2, "0")}
            </span>
          )}
          <span className="font-display text-[21px] font-normal leading-tight text-text-dark">
            {title}
          </span>
        </h2>
        {hint && (
          <span className="flex-none font-mono text-[10px] uppercase tracking-[0.12em] text-text-secondary">
            {hint}
          </span>
        )}
      </div>
      {children}
    </Reveal>
  );
}

/** A small labelled key/value pair. */
export function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-maroon-soft">
        {label}
      </dt>
      <dd className="text-[14px] leading-relaxed text-text-body">
        {value ?? <span className="text-text-secondary">&mdash;</span>}
      </dd>
    </div>
  );
}

/**
 * A headline number: display serif in maroon over a mono caption, counting up
 * when it scrolls into view.
 */
export function Figure({
  value,
  caption,
  format = (n) => Math.round(n).toLocaleString("en-IN"),
  size = "md",
}: {
  value: number;
  caption: string;
  format?: (value: number) => string;
  size?: "sm" | "md" | "lg";
}) {
  const sizes = {
    sm: "text-[26px]",
    md: "text-[40px]",
    lg: "text-[54px]",
  } as const;
  return (
    <div>
      <CountUp
        value={value}
        format={format}
        className={`figure block ${sizes[size]}`}
      />
      <p className="mt-2 font-mono text-[9.5px] uppercase tracking-[0.14em] text-text-secondary">
        {caption}
      </p>
    </div>
  );
}

type BadgeTone = "maroon" | "neutral" | "positive" | "negative";

const badgeTones: Record<BadgeTone, string> = {
  maroon: "border-maroon bg-maroon text-white",
  neutral: "border-hair bg-surface text-text-secondary",
  positive: "border-positive/40 bg-positive-soft text-positive",
  negative: "border-negative/30 bg-negative/10 text-negative",
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
      className={`inline-flex items-center rounded-full border px-2.5 py-[3px] font-mono text-[9.5px] uppercase tracking-[0.12em] ${badgeTones[tone]}`}
    >
      {children}
    </span>
  );
}

/** A note set off by a maroon rule — caveats, "how to read this". */
export function Note({
  label,
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  return (
    <p className="border-l-2 border-maroon/30 pl-3 text-[12.5px] leading-relaxed text-text-secondary">
      {label && (
        <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-maroon-soft">
          {label}{" "}
        </span>
      )}
      {children}
    </p>
  );
}
