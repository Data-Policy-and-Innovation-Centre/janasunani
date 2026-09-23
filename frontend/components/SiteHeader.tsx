"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ScrollProgress } from "./motion";

const NAV = [
  ["/", "Submit"],
  ["/history", "History"],
  ["/supervisor", "Supervisor"],
] as const;

/**
 * The Janasunani mark: a dot with arcs opening outward from it — a grievance
 * being heard. The outer ring turns slowly; it stops under reduced motion.
 */
function BrandGlyph() {
  return (
    <svg
      viewBox="0 0 32 32"
      className="h-8 w-8 flex-none"
      aria-hidden="true"
      fill="none"
    >
      <circle
        cx="16"
        cy="16"
        r="15"
        stroke="var(--dpic-maroon)"
        strokeWidth="1"
        opacity="0.35"
      />
      <g className="glyph-spin">
        {[0, 90, 180, 270].map((angle) => (
          <path
            key={angle}
            d="M16 5.5 A10.5 10.5 0 0 1 26.5 16"
            stroke="var(--dpic-maroon)"
            strokeWidth="1.25"
            strokeLinecap="round"
            transform={`rotate(${angle} 16 16)`}
            opacity={angle === 0 ? 0.9 : 0.3}
          />
        ))}
      </g>
      <circle cx="16" cy="16" r="3.5" fill="var(--dpic-maroon)" />
    </svg>
  );
}

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="appbar">
      <div className="mx-auto flex h-16 max-w-[1240px] items-center gap-4 px-5 sm:gap-8 sm:px-7">
        <Link href="/" className="flex flex-none items-center gap-3">
          <BrandGlyph />
          <span className="leading-none">
            <span className="block font-display text-[19px] font-medium tracking-[-0.01em] text-text-dark">
              Janasunani
            </span>
            <span className="mt-1 block font-mono text-[8.5px] uppercase tracking-[0.16em] text-text-secondary">
              Grievance Redressal
            </span>
          </span>
        </Link>

        {/* min-w-0 lets the nav shrink below its content width so a narrow
            screen scrolls the nav itself rather than the whole page. */}
        <nav
          className="no-scrollbar flex min-w-0 flex-1 items-stretch gap-1 self-stretch overflow-x-auto"
          aria-label="Primary"
        >
          {NAV.map(([href, label]) => {
            const active =
              href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                data-active={active}
                aria-current={active ? "page" : undefined}
                className={`nav-link flex flex-none items-center px-3.5 text-[13.5px] font-medium ${
                  active ? "text-maroon" : "text-text-secondary hover:text-maroon"
                }`}
              >
                {label}
              </Link>
            );
          })}
        </nav>

        <span className="hidden flex-none font-mono text-[8.5px] uppercase tracking-[0.16em] text-text-secondary md:block">
          Univ. of Chicago &times; Govt. of Odisha
        </span>
      </div>
      <ScrollProgress />
    </header>
  );
}
