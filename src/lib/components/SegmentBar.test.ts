import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import SegmentBar from "./SegmentBar.svelte";

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
	// same status in the same place while a transfer runs.
	it("keeps the statuses in their fixed order", () => {
		render(SegmentBar, { props: { counts: { skip: 1, transferred: 2, pending: 3 } } });

		const order = screen.getAllByTestId("segment").map((segment) => segment.getAttribute("data-status"));
		expect(order).toEqual(["pending", "transferred", "skip"]);
	});

	// A status the plan holds no tracks in takes no room in the bar.
	it("gives a status with no tracks no segment", () => {
		render(SegmentBar, { props: { counts: { pending: 3, transferred: 0 } } });

		expect(screen.getAllByTestId("segment")).toHaveLength(1);
	});
});
