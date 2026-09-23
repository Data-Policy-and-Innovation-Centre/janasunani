"use client";

import {
  useEffect,
  useLayoutEffect,
  useRef,
  type CSSProperties,
  type ElementType,
  type ReactNode,
} from "react";

/**
 * These components drive the DOM node directly rather than holding animation
 * progress in React state: a counting figure would otherwise call setState on
 * every frame, and the reveal would re-render a subtree to add one class.
 */

/** useLayoutEffect on the client, useEffect on the server (where it is a no-op). */
const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

/** True when the visitor has asked the OS to reduce motion. */
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Whether we can observe this element entering the viewport at all. */
function canObserve(): boolean {
  return typeof IntersectionObserver !== "undefined";
}

/**
 * Reveals its children once they first scroll into view.
 *
 * The element carries `.reveal` from the first render so it is offset before
 * the observer fires; `.in` is added once and never removed, so content does
 * not re-animate when scrolled back past. Under reduced motion, or with no
 * IntersectionObserver, it is revealed immediately — the content is never
 * gated on the animation running.
 */
export function Reveal({
  children,
  delay = 0,
  as: Tag = "div",
  className = "",
}: {
  children: ReactNode;
  /** Stagger, in milliseconds, against the other reveals in the same group. */
  delay?: number;
  as?: ElementType;
  className?: string;
}) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (prefersReducedMotion() || !canObserve()) {
      node.classList.add("in");
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            observer.disconnect();
          }
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <Tag
      ref={ref}
      className={`reveal ${className}`.trim()}
      style={{ "--reveal-delay": `${delay}ms` } as CSSProperties}
    >
      {children}
    </Tag>
  );
}

/**
 * Counts a figure up to its value when it scrolls into view.
 *
 * The settled value is rendered as the element's children, so it is present in
 * the server-rendered HTML and is what shows with JavaScript disabled or motion
 * reduced. When the count will run, the text is zeroed before the browser
 * paints and then animated frame by frame.
 *
 * `format` renders every intermediate frame as well as the settled one, so the
 * units never change between them.
 */
export function CountUp({
  value,
  format,
  duration = 1000,
  className = "",
}: {
  value: number;
  format: (value: number) => string;
  duration?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);

  // Held in a ref so a new inline formatter each render does not restart the
  // animation; the effects depend on the value, not the function identity.
  // Synced in its own effect (never during render) and declared first, so it
  // is current by the time the effects below read it.
  const formatRef = useRef(format);
  useIsomorphicLayoutEffect(() => {
    formatRef.current = format;
  });

  const willAnimate = useRef(false);

  // Zero the text before paint so an element that is already on screen does
  // not flash its settled value before counting up.
  useIsomorphicLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    willAnimate.current = !prefersReducedMotion() && canObserve();
    if (willAnimate.current) node.textContent = formatRef.current(0);
  }, [value]);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (!willAnimate.current) {
      node.textContent = formatRef.current(value);
      return;
    }

    let frame = 0;
    const run = () => {
      const start = performance.now();
      const tick = (now: number) => {
        const t = Math.min(1, (now - start) / duration);
        // Same shape as --ease, so the count settles like everything else.
        const eased = 1 - Math.pow(1 - t, 3);
        node.textContent = formatRef.current(value * eased);
        if (t < 1) frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    };

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            observer.disconnect();
            run();
          }
        }
      },
      // Threshold 0, so the count starts the moment any part of the figure
      // enters. Reveal uses 0.05 with a negative rootMargin and so always
      // fires later: that ordering matters, because a figure sitting at its
      // pre-count 0 while already on screen would read as a real zero, and
      // for these measures ("No action for 7 days or more") zero is a
      // plausible value rather than an obvious placeholder.
      { threshold: 0 },
    );
    observer.observe(node);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [value, duration]);

  return (
    <span ref={ref} className={className}>
      {format(value)}
    </span>
  );
}

/** The reading-position bar pinned under the appbar rule. */
export function ScrollProgress() {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const update = () => {
      const scrollable =
        document.documentElement.scrollHeight - window.innerHeight;
      const pct = scrollable <= 0 ? 0 : (window.scrollY / scrollable) * 100;
      node.style.width = `${pct}%`;
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, []);

  return (
    <div
      ref={ref}
      className="scroll-progress"
      style={{ width: 0 }}
      aria-hidden="true"
    />
  );
}
