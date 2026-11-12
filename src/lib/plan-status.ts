/**
 * The plan's status vocabulary: the order the statuses appear in, the label
 * each one reads as and the colour each one wears. The bar and the tiles read
 * all three from here, so the two cannot disagree about any of them.
 */

/**
 * The order a track moves through, fixed rather than sorted by count: rows
 * that swap places as a transfer runs are unreadable at the moment the
 * numbers finally matter.
 */
export const PLAN_STATUS_ORDER = ["pending", "transferred", "needs_review", "failed", "skip"] as const;

export const PLAN_STATUS_LABELS: Record<string, string> = {
	pending: "Pending",
	transferred: "Transferred",
	needs_review: "Needs review",
	failed: "Failed",
	skip: "Skipped",
};

/**
 * The colours are class strings rather than colour values, because Tailwind
 * finds a class it can read in the source and cannot find one assembled at
 * runtime. A colour computed from a value compiles to nothing.
 */
const PLAN_STATUS_FILL: Record<string, string> = {
	pending: "bg-fill-pending",
	transferred: "bg-brand",
	needs_review: "bg-state-warn",
	failed: "bg-state-bad",
	skip: "bg-surface-subtle",
};

const PLAN_STATUS_TEXT: Record<string, string> = {
	pending: "text-text-pending",
	transferred: "text-brand",
	needs_review: "text-state-warn",
	failed: "text-state-bad",
	skip: "text-text-faint",
};

/** A status this build does not know wears the neutral colours. */
const UNKNOWN_FILL = "bg-surface-subtle";
const UNKNOWN_TEXT = "text-text-muted";

export type PlanSegment = { key: string; label: string; count: number; fill: string; text: string };

/**
 * The statuses the plan holds, in `PLAN_STATUS_ORDER`, followed by any status
 * this build does not know, sorted by key.
 *
 * A status the plan holds but this build does not know is still a status of the
 * plan: dropping it would make the total disagree with the database it was
 * read from, which is the one number the screen cannot be wrong about.
 */
export function planSegments(counts: Record<string, number>): PlanSegment[] {
	const known = PLAN_STATUS_ORDER.filter((key) => (counts[key] ?? 0) > 0);
	const unknown = Object.keys(counts)
		.filter((key) => !(PLAN_STATUS_ORDER as readonly string[]).includes(key))
		.filter((key) => (counts[key] ?? 0) > 0)
		.sort();
	return [...known, ...unknown].map((key) => ({
		key,
		label: PLAN_STATUS_LABELS[key] ?? key,
		count: counts[key] ?? 0,
		fill: PLAN_STATUS_FILL[key] ?? UNKNOWN_FILL,
		text: PLAN_STATUS_TEXT[key] ?? UNKNOWN_TEXT,
	}));
}

/** How many tracks the plan holds, whatever statuses they are in. */
export function planTotal(counts: Record<string, number>): number {
	return Object.values(counts).reduce((sum, value) => sum + value, 0);
}
