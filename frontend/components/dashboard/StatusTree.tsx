"use client";

import { useEffect, useState } from "react";
import { fetchStatus } from "@/lib/api";
import { sharePct, type StatusSummary } from "@/lib/dashboard";

type Branch = "open" | "disposed";

/** One outcome under the total: its count and share. Open and Disposed open
 * their tab; Discarded has none, so it is not a button. */
function Node({ label, n, total, onOpen, children }: {
  label: string;
  n: number;
  total: number;
  onOpen?: () => void;
  children?: React.ReactNode;
}) {
  const body = (
    <>
      <span className="block text-[17px] text-text-body">
        {label} {onOpen && <span className="text-maroon">›</span>}
      </span>
      <span className="figure mt-1 block text-[38px] leading-none">{n.toLocaleString("en-IN")}</span>
      <span className="mt-1.5 block text-[15px] text-text-secondary">{sharePct(n, total)} of all cases</span>
      {children}
    </>
  );
  const box = "block w-full rounded border border-hair bg-surface px-5 py-4 text-center";
  return onOpen ? (
    <button type="button" onClick={onOpen} className={`${box} transition-colors hover:border-maroon hover:bg-maroon-wash/40 focus-visible:outline-2 focus-visible:outline-maroon`}>
      {body}
    </button>
  ) : (
    <div className={box}>{body}</div>
  );
}

/** Every case filed in scope, then how each one stands, drawn as a tree:
 * the total above, the three outcomes it splits into below. */
export function StatusTree({ office, year, onOpen }: { office: string; year: string; onOpen: (b: Branch) => void }) {
  const [tree, setTree] = useState<StatusSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchStatus(office || undefined, year || undefined)
      .then((t) => {
        if (cancelled) return;
        setTree(t);
        setError(null);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [office, year]);

  if (error) return <p className="text-[16px] text-negative">{error}</p>;
  if (!tree) return <div className="h-[260px]" />;
  return (
    <section aria-label="Cases by status">
      <div className="mx-auto w-fit text-center">
        <p className="text-[17px] text-text-secondary">Cases filed · {tree.period}</p>
        <p className="figure mt-1 text-[56px] leading-none">{tree.total.toLocaleString("en-IN")}</p>
      </div>
      {/* The connectors: a stem down from the total, a rail across the three
          outcomes, and a stem down to each. */}
      <div className="mx-auto mt-4 h-6 w-px bg-greystone-light" />
      <div className="relative grid gap-4 sm:grid-cols-3">
        <div className="absolute left-[16.67%] right-[16.67%] top-0 hidden h-px bg-greystone-light sm:block" />
        {(
          [
            ["open", "Open", tree.open],
            ["disposed", "Disposed", tree.disposed],
            ["discarded", "Discarded", tree.discarded],
          ] as const
        ).map(([id, label, n]) => (
          <div key={id}>
            <div className="mx-auto hidden h-6 w-px bg-greystone-light sm:block" />
            <Node
              label={label}
              n={n}
              total={tree.total}
              onOpen={id === "discarded" ? undefined : () => onOpen(id)}
            >
              {id === "disposed" && (
                <span className="mt-2 block border-t border-hair-soft pt-2 text-[15px] text-text-secondary">
                  With benefit:{" "}
                  <span className="font-medium text-text-dark">{tree.disposedWithBenefit.toLocaleString("en-IN")}</span>{" "}
                  ({sharePct(tree.disposedWithBenefit, tree.disposed)})
                </span>
              )}
            </Node>
          </div>
        ))}
      </div>
    </section>
  );
}
