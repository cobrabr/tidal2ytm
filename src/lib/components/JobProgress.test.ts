import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import JobProgress from "./JobProgress.svelte";
import type { Progress } from "../jobs";

const base: Progress = { jid: 1, kind: "sync", state: "running", done: 5, total: 10 };

describe("JobProgress", () => {
	it("cancelling and cancelled read differently", () => {
		const { unmount } = render(JobProgress, { props: { progress: { ...base, state: "cancelling" } } });
		expect(screen.getByText("cancelling")).toBeTruthy();
		unmount();
		render(JobProgress, { props: { progress: { ...base, state: "cancelled" } } });
		expect(screen.getByText("cancelled")).toBeTruthy();
	});

	it("renders no denominator when total is null", () => {
		render(JobProgress, { props: { progress: { ...base, total: null } } });
		expect(screen.queryByText(/\/ 10/)).toBeNull();
		expect(screen.getByText("5")).toBeTruthy();
	});

	// Tidal's favourites endpoint reports no count, and the count it can report
	// includes tracks it will not hand back. A bar drawn against a total the run
	// does not have would claim to know how much is left.
	it("draws no bar while the total is unknown", () => {
		render(JobProgress, { props: { progress: { ...base, total: null } } });
		expect(screen.queryByLabelText("job progress")).toBeNull();
		expect(screen.getByText("5")).toBeTruthy();
	});

	// The row is already a live region, so the spinner beside the state text is
	// decoration: a second announced region would say the same thing twice.
	it("draws the shared spinner as decoration", () => {
		const { container } = render(JobProgress, { props: { progress: base } });
		const icons = [...container.querySelectorAll("svg")];
		expect(icons.length).toBeGreaterThan(0);
		expect(icons.every((icon) => icon.getAttribute("aria-hidden") === "true")).toBe(true);
	});
});
