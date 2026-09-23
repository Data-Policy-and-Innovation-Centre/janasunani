/**
 * Plain-language labels for the monitoring screen.
 *
 * The aggregate artifact is written for analysts: "Tiling-sample coverage",
 * "Bare disposal / templated closures", "Deepest-office repeat arrival". The
 * people reading this screen are grievance officers, many of whom do not read
 * technical English comfortably.
 *
 * These translate for display only, keyed by the stable panel and metric ids.
 * Renaming at source would cost the analysts the precise wording their notes
 * depend on, and would need a regenerated release for a wording change.
 *
 * Anything with no entry falls through to the artifact's own label, so a
 * metric added upstream shows its analyst name rather than disappearing.
 *
 * The exact definition of each measure still travels with it, in the
 * "How to read this" note under every panel. Simplifying the label does not
 * remove the caveat.
 */

export type Lang = "en" | "or";

interface Dictionary {
  /** Panel titles, by panel id. */
  panels: Record<string, string>;
  /** Metric labels, by metric id. */
  metrics: Record<string, string>;
  /** Breakdown row labels, by the artifact's own label. */
  rows: Record<string, string>;
  /**
   * Denominator captions carry a date or period ("Open at 30 July 2025"), so
   * they are rewritten by ordered phrase replacement rather than exact match.
   * That keeps them working when the snapshot date moves.
   */
  denominatorPhrases: [string, string][];
}

const EN: Dictionary = {
  panels: {
    flow: "From filing to closure",
    aging: "Cases waiting",
    transfers: "Cases moved between offices",
    journey: "How long a case takes",
    atr: "Action taken reports and review",
    demand: "How many cases, and how many are repeats",
    closure: "How cases were closed",
    discards: "Why cases were discarded, and when",
    recording: "What the records hold",
    offices: "By district and office",
  },
  metrics: {
    // Cases waiting
    "inactive-7": "No action for 7 days or more",
    "escalation-passed": "Past the due date",
    // Cases moved between offices
    "transfer-rate": "Share of cases transferred",
    "loop-rate": "Came back to the same office",
    "followup-proxy": "No follow-up within 7 days",
    // How long a case takes. The mean leads because it is the total the five
    // phases add up to; the median is phrased so it cannot be mistaken for it.
    "mean-total": "Average time to close",
    "median-total": "Half of cases close within",
    "tiling-coverage": "Cases with usable dates",
    // How many cases
    filings: "Cases filed",
    problems: "Separate problems",
    citizens: "Citizens who filed",
    "duplicate-adjustment": "Repeat filings",
    "repeat-groups": "Problems filed more than once",
    campaigns: "Group complaints",
    // How cases were closed
    "bare-ladder": "Closed with no action recorded",
    "bare-resolved": "Closed with no action, of all closed",
    "action-recorded": "Closed with an action recorded",
    "benefit-recorded": "Closed with a benefit recorded",
    reopened: "Reopened or sent back",
    // Action taken reports and review
    "review-required": "Workflow requires review",
    "atr-replied": "Report submitted",
    "review-done": "Required review happened",
    "closed-without-review": "Closed without the required review",
    "atr-sent-back": "Report sent back",
    "atr-standard-reason": "Sent back with a standard reason",
    "atr-waiting": "Reports waiting for the next office",
    "atr-wait": "Typical wait of those reports",
    "refiling-30": "Same problem filed again within 30 days",
    "refiling-90": "Same problem filed again within 90 days",
    // Why cases were discarded
    "discard-rate": "Share of cases discarded",
    "discard-reason-recognised": "Discards with a standard reason",
    "discard-after-transfer": "Reason given after a transfer",
  },
  rows: {
    // Journey phases. The aging buckets ("0-6 days") are already plain and
    // are deliberately left alone.
    Registration: "Registration",
    "First assignment": "Sent to the first officer",
    "Field action": "Work in the field",
    Review: "Sent back for review",
    Closure: "Closing",
  },
  denominatorPhrases: [
    ["Disposed journeys that tile", "Closed cases with usable dates"],
    ["All resolved in", "All cases closed in"],
    ["Grievances created in", "Cases filed in"],
    ["Filings in", "Cases filed in"],
    ["Open at", "Open cases at"],
    [" cohort", ""],
  ],
};

/**
 * Odia. Empty until the department supplies wording it actually uses; an
 * empty dictionary keeps the language out of `availableLanguages`, so no
 * toggle appears that would silently fall back to English.
 */
const OR: Dictionary = {
  panels: {},
  metrics: {},
  rows: {},
  denominatorPhrases: [],
};

const DICTIONARIES: Record<Lang, Dictionary> = { en: EN, or: OR };

export const LANG_NAMES: Record<Lang, string> = {
  en: "English",
  or: "ଓଡ଼ିଆ",
};

function isPopulated(dictionary: Dictionary): boolean {
  return (
    Object.keys(dictionary.panels).length > 0 ||
    Object.keys(dictionary.metrics).length > 0
  );
}

/**
 * The languages with enough wording to be worth offering. English is always
 * present; another language appears only once it has entries.
 */
export function availableLanguages(): Lang[] {
  return (Object.keys(DICTIONARIES) as Lang[]).filter(
    (lang) => lang === "en" || isPopulated(DICTIONARIES[lang]),
  );
}

/** The panel's plain title, or the artifact's own when none is mapped. */
export function panelTitle(id: string, fallback: string, lang: Lang = "en"): string {
  return DICTIONARIES[lang].panels[id] ?? EN.panels[id] ?? fallback;
}

/** The metric's plain label, or the artifact's own when none is mapped. */
export function metricLabel(id: string, fallback: string, lang: Lang = "en"): string {
  return DICTIONARIES[lang].metrics[id] ?? EN.metrics[id] ?? fallback;
}

/** A breakdown row's plain label, or the artifact's own when none is mapped. */
export function rowLabel(label: string, lang: Lang = "en"): string {
  return DICTIONARIES[lang].rows[label] ?? EN.rows[label] ?? label;
}

/**
 * Rewrites a denominator caption phrase by phrase, longest-lived rules first.
 * An unmatched caption is returned unchanged.
 */
export function denominatorLabel(label: string, lang: Lang = "en"): string {
  const phrases = DICTIONARIES[lang].denominatorPhrases.length
    ? DICTIONARIES[lang].denominatorPhrases
    : EN.denominatorPhrases;
  let out = label;
  for (const [from, to] of phrases) {
    out = out.split(from).join(to);
  }
  return out.trim();
}
