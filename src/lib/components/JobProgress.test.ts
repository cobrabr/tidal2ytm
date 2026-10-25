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
});
