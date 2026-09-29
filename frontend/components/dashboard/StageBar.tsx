import { fmtDays, stageSegments, visibleStages, type PhaseKey } from "@/lib/dashboard";

/** One bar split into the five phases, as in the bottleneck notes. The
 * segments are means, so they add up to the average. */
export function StageBar({ phases, compact = false }: { phases: Record<PhaseKey, number>; compact?: boolean }) {
  return (
    <div
      className={`flex w-full overflow-hidden rounded-[3px] ${compact ? "h-7" : "h-11"}`}
      role="img"
      aria-label={stageSegments(phases).map((s) => `${s.label} ${fmtDays(s.days)}`).join(", ")}
    >
      {stageSegments(phases).map((s) =>
        s.pct > 0 ? (
          <div
            key={s.key}
            title={`${s.label}: ${fmtDays(s.days)} (${Math.round(s.pct)}%)`}
            className={`flex items-center justify-center overflow-hidden whitespace-nowrap tabular-nums ${compact ? "text-[13px]" : "text-[14px]"}`}
            style={{ width: `${s.pct}%`, background: s.color, color: s.ink }}
          >
            {s.labelled && `${Math.round(s.days)}d`}
          </div>
        ) : null,
      )}
    </div>
  );
}

/** A key to the stage colours, listing only stages that show in `bars`. */
export function StageLegend({ bars }: { bars: Record<PhaseKey, number>[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5">
      {visibleStages(bars).map((p) => (
        <li key={p.key} className="flex items-center gap-1.5 text-[15px] text-text-secondary">
          <span className="h-3.5 w-3.5 rounded-[2px]" style={{ background: p.color }} />
          {p.label}
        </li>
      ))}
    </ul>
  );
}
