import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Home from "./Home.svelte";
import { getAuthStatus, getMeta, getStatusCounts } from "../lib/ipc";
import { subscribeJobs } from "../lib/jobs";
import type { Progress } from "../lib/jobs";
import type { AuthStatus } from "../lib/types";

// Mocked at the screen's own boundary, the command it asks. Stubbing `invoke`
// instead would test that the Tauri API is shaped the way this file assumes,
// which is the host's contract and not the screen's; and rendering the card
// directly — what `AuthStatus.test.ts` does — cannot see a screen that stopped
// rendering it at all, which is the defect this file exists for.
vi.mock("../lib/ipc");
vi.mock("../lib/jobs");

const answer = vi.mocked(getAuthStatus);
const counts = vi.mocked(getStatusCounts);
const jobs = vi.mocked(subscribeJobs);

/** The handler the screen registered, so a test can deliver a job event. */
let push: ((progress: Progress) => void) | null = null;

/** Asymmetric on purpose: one signed-in provider makes a swap visible. */
const BOTH_SIGNED_OUT: AuthStatus = {
	tidal: { status: "signed_out", expires_at: null, has_client_secret: true },
	ytm: { status: "signed_out", expires_at: null, has_client_secret: false },
};

const MIXED: AuthStatus = {
	tidal: { status: "signed_out", expires_at: "2020-01-01T00:00:00Z", has_client_secret: true },
	ytm: { status: "valid", expires_at: null, has_client_secret: false },
};

const statesOf = async () =>
	(await screen.findAllByRole("status")).map((element) => element.getAttribute("data-state"));

describe("Home", () => {
	beforeEach(() => {
		answer.mockReset();
		counts.mockReset();
		counts.mockResolvedValue({});
		vi.mocked(getMeta).mockReset();
		vi.mocked(getMeta).mockResolvedValue(null);
		push = null;
		jobs.mockReset();
		jobs.mockImplementation((handler) => {
			push = handler;
			return () => {};
		});
	});

	// Two cards side by side is the first time a provider's label means anything:
	// with one card, dropping it loses nothing. Asserted through the rendered
	// state rather than the label text, so rewording a provider cannot break it.
	it("renders one card per provider, in the order the contract names them", async () => {
		answer.mockResolvedValue(MIXED);

		render(Home);

		expect(await statesOf()).toEqual(["signed-out", "valid"]);
	});

	// Without this the two cards are interchangeable: a swapped pair, or a label
	// dropped from both, looks exactly like a working screen and sends the user
	// to re-authenticate a provider that was fine. The names are compared to each
	// other rather than to a literal, so renaming a provider is not a test failure.
	it("gives each card a distinct accessible name", async () => {
		answer.mockResolvedValue(BOTH_SIGNED_OUT);

		render(Home);

		const groups = await screen.findAllByRole("group");
		const names = groups.map((group) => group.getAttribute("aria-label"));
		expect(names).toHaveLength(2);
		expect(new Set(names).size).toBe(2);
	});

	// The screen's own half of the failure contract: whatever the boundary hands it
	// arrives as an `Error`, and the text in it is what the user reads. Whether a
	// bare-string rejection from Tauri becomes one is `ipc.test.ts`'s job, and
	// duplicating it here is how this file came to assert against a rejection
	// shape the real boundary never produces.
	it("shows the explanation the boundary hands it", async () => {
		const message = "the sidecar started and then stopped: sidecar_lost";
		answer.mockRejectedValue(new Error(message));

		render(Home);

		const alert = await screen.findByRole("alert");
		expect(alert.textContent).toContain(message);
		expect(screen.queryAllByRole("status")).toHaveLength(0);
	});

	// A failure carrying nothing readable must still leave the loading state.
	// Without the `finally` the user sits on "Checking provider sign-in…" forever
	// with nothing to retry and no reason given, and the window looks busy.
	it("leaves the loading state when a failure carries nothing readable", async () => {
		answer.mockRejectedValue(undefined);

		render(Home);

		expect(await screen.findByRole("alert")).toBeTruthy();
		expect(screen.queryAllByRole("status")).toHaveLength(0);
	});

	// Two numbers about the same library, one screen: a heading that says 2,287
	// above tiles that add up to something else is the sort of disagreement that
	// makes a person distrust the whole screen. Only statuses the store actually
	// holds get a tile, so the tiles add up to what the heading says.
	it("agrees with itself about how many tracks the library holds", async () => {
		answer.mockResolvedValue(BOTH_SIGNED_OUT);
		counts.mockResolvedValue({ pending: 12, transferred: 3, failed: 1 });

		render(Home);

		expect(await screen.findByText("16")).toBeTruthy();
		expect(await screen.findByText("12")).toBeTruthy();
		// One tile per status the plan holds: no total tile, and no tile for a
		// status with no tracks. The total belongs to the page head, so the
		// tiles' counts must add up to the number the head says.
		const tiles = screen.getAllByRole("definition").map((tile) => Number(tile.textContent));
		expect(tiles).toHaveLength(3);
		expect(tiles.reduce((sum, value) => sum + value, 0)).toBe(16);
	});

	// An empty mirror and a mirror that was never fetched are different facts, so
	// the empty one has to say so rather than render as a section with no rows.
	it("says the plan is empty rather than listing nothing", async () => {
		answer.mockResolvedValue(BOTH_SIGNED_OUT);

		render(Home);

		expect(await screen.findByText(/no tracks yet/i)).toBeTruthy();
		expect(screen.queryAllByRole("term")).toHaveLength(0);
	});

	// A job that has finished is the state a person actually looks for: showing
	// it only while running is how a sub-second sync left nothing on screen at
	// all, and the count it reached went with it.
	it("keeps a finished job visible with the count it reached", async () => {
		answer.mockResolvedValue(BOTH_SIGNED_OUT);
		counts.mockResolvedValue({ pending: 100 });

		render(Home);

		await screen.findAllByRole("group");
		await vi.waitFor(() => expect(push).not.toBeNull());
		push?.({ jid: 1, kind: "sync", state: "done", done: 100, total: 100 });

		const bar = (await screen.findByLabelText("job progress")) as HTMLProgressElement;
		expect(bar.max).toBe(100);
		expect(bar.value).toBe(100);
	});
});
