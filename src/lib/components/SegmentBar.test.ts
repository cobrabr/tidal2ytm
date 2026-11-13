import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import SegmentBar from "./SegmentBar.svelte";
import { PLAN_STATUS_ORDER } from "../plan-status";

/** The statuses of the rendered bar, in the order they appear. */
function renderedOrder(): (string | null)[] {
	return screen.getAllByTestId("segment").map((segment) => segment.getAttribute("data-status"));
}

describe("SegmentBar", () => {
	// A status this build does not know is still a status of the plan: a bar
	// that hides it makes the total disagree with the database the counts were
	// read from, and the user cannot tell which of the two numbers is wrong.
	it("gives a status this build does not know a segment of its own", () => {
		render(SegmentBar, { props: { counts: { pending: 3, something_new: 2 } } });

		const segments = screen.getAllByTestId("segment");
		expect(segments).toHaveLength(2);
	});

	// An empty bar reads as a bar that has not started, which is a different
	// claim from a plan that holds nothing.
	it("renders nothing when the plan holds no tracks", () => {
		const { container } = render(SegmentBar, { props: { counts: {} } });

		expect(screen.queryAllByTestId("segment")).toHaveLength(0);
		expect(container.firstElementChild).toBeNull();
	});

	// The order is fixed rather than sorted by count, so the eye can find the
	// same status in the same place while a transfer runs. The expected order
	// is read from the vocabulary rather than spelled out, so the test fails
	// when the order changes instead of repeating it. The counts are out of
	// order on purpose, so a bar sorted by count comes out differently.
	it("keeps the statuses in their fixed order", () => {
		const counts: Record<string, number> = Object.fromEntries(
			PLAN_STATUS_ORDER.map((key, index) => [key, [3, 1, 4, 2, 5][index] ?? 1]),
		);
		render(SegmentBar, { props: { counts } });

		expect(renderedOrder()).toEqual([...PLAN_STATUS_ORDER]);
	});

	// A status this build does not know comes after the ones it knows: the
	// known statuses keep the places the eye already knows them by.
	it("puts a status this build does not know after the ones it knows", () => {
		const [first, second] = PLAN_STATUS_ORDER;
		render(SegmentBar, { props: { counts: { something_new: 2, [first]: 3, [second]: 1 } } });

		expect(renderedOrder()).toEqual([first, second, "something_new"]);
	});

	// Two statuses this build does not know have no order of their own, so
	// they sort by key: the same plan always draws the same bar.
	it("sorts two statuses this build does not know by key", () => {
		render(SegmentBar, { props: { counts: { zebra: 1, apple: 2 } } });

		expect(renderedOrder()).toEqual(["apple", "zebra"]);
	});

	// A status the plan holds no tracks in takes no room in the bar.
	it("gives a status with no tracks no segment", () => {
		render(SegmentBar, { props: { counts: { pending: 3, transferred: 0 } } });

		expect(screen.getAllByTestId("segment")).toHaveLength(1);
	});

	// The bar is a shape, not information: the counts beside it are the
	// readable form, and a row of coloured blocks tells a screen reader
	// nothing it can use. That is behaviour, not styling.
	it("hides the segments from assistive technology", () => {
		render(SegmentBar, { props: { counts: { pending: 3 } } });

		expect(screen.getByTestId("segment").getAttribute("aria-hidden")).toBe("true");
	});
});
