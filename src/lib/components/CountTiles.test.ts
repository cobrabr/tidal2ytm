import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import CountTiles from "./CountTiles.svelte";
import { PLAN_STATUS_LABELS } from "../plan-status";

describe("CountTiles", () => {
	// The tiles are a definition list, so the counts can be read and summed
	// from the DOM: the heading above them says the same total, and two
	// numbers about the same library must not disagree.
	it("renders a tile per status the plan holds", () => {
		render(CountTiles, { props: { counts: { pending: 3, transferred: 1 } } });

		expect(screen.getAllByRole("definition")).toHaveLength(2);
	});

	it("shows each count and the label it belongs to", () => {
		render(CountTiles, { props: { counts: { pending: 3, transferred: 1 } } });

		expect(screen.getAllByRole("definition").map((value) => value.textContent)).toEqual(["3", "1"]);
		expect(screen.getByText(PLAN_STATUS_LABELS.pending)).toBeTruthy();
		expect(screen.getByText(PLAN_STATUS_LABELS.transferred)).toBeTruthy();
	});

	// A status this build does not know is still a status of the plan: a tile
	// that drops it makes the tiles' total disagree with the database.
	it("gives a status this build does not know a tile", () => {
		render(CountTiles, { props: { counts: { pending: 3, something_new: 2 } } });

		expect(screen.getAllByRole("definition")).toHaveLength(2);
	});
});
