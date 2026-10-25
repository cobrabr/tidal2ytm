import { listen } from "@tauri-apps/api/event";
import type { UnlistenFn } from "@tauri-apps/api/event";

export type Progress = {
	jid: number;
	kind: string;
	state: "running" | "cancelling" | "cancelled" | "done" | "failed";
	done: number;
	total: number | null;
};

/**
 * Subscribe to job progress events. Returns an unlisten function.
 *
 * The event name must match the host's `JOB_PROGRESS_EVENT` constant
 * ("job-progress"). A mismatch silently strands every listener.
 */
export function subscribeJobs(handler: (p: Progress) => void): () => void {
	let unlisten: UnlistenFn | null = null;
	let cancelled = false;

	listen<Progress>("job-progress", (event) => {
		if (cancelled) return;
		handler(event.payload);
	})
		.then((fn) => {
			if (cancelled) {
				fn();
			} else {
				unlisten = fn;
			}
		})
		.catch(() => {
			// ignore
		});

	return () => {
		cancelled = true;
		if (unlisten) unlisten();
	};
}
