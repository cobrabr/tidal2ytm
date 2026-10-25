<script lang="ts">
	import { onMount, onDestroy } from "svelte";
	import ProviderRow from "../lib/components/ProviderRow.svelte";
	import JobProgress from "../lib/components/JobProgress.svelte";
	import {
		UNREADABLE_STATUS,
		getAuthStatus,
		beginAuth,
		getStatusCounts,
		getMeta,
		startSync,
		cancelJob,
	} from "../lib/ipc";
	import { subscribeJobs, type Progress } from "../lib/jobs";
	import type { AuthStatus as AuthStatusData } from "../lib/types";

	let status = $state<AuthStatusData | null>(null);
	let failure = $state<string | null>(null);
	let loading = $state(true);

	let statusCounts = $state<Record<string, number> | null>(null);
	let syncStartedAt = $state<string | null>(null);
	let syncLastCompletedAt = $state<string | null>(null);

	let jobProgress = $state<Progress | null>(null);
	let syncing = $state(false);

	let signingIn = $state<string | null>(null);

	let unlistenJobs: (() => void) | null = null;

	onDestroy(() => {
		if (unlistenJobs) unlistenJobs();
	});

	onMount(async () => {
		try {
			status = await getAuthStatus();
		} catch (e) {
			failure = e instanceof Error ? e.message : UNREADABLE_STATUS;
		} finally {
			loading = false;
		}

		try {
			statusCounts = await getStatusCounts();
		} catch {
			// ignore
		}

		try {
			syncStartedAt = await getMeta("sync_started_at");
			syncLastCompletedAt = await getMeta("sync_last_completed_at");
		} catch {
			// ignore
		}

		unlistenJobs = subscribeJobs((p) => {
			jobProgress = p;
			if (p.state === "done" || p.state === "failed" || p.state === "cancelled") {
				syncing = false;
				// re-read meta
				getMeta("sync_started_at")
					.then((v) => (syncStartedAt = v))
					.catch(() => {});
				getMeta("sync_last_completed_at")
					.then((v) => (syncLastCompletedAt = v))
					.catch(() => {});
				getStatusCounts()
					.then((v) => (statusCounts = v))
					.catch(() => {});
			}
		});
	});

	async function signIn(provider: string) {
		signingIn = provider;
		try {
			await beginAuth(provider);
			status = await getAuthStatus();
		} catch (e) {
			failure = e instanceof Error ? e.message : UNREADABLE_STATUS;
		} finally {
			signingIn = null;
		}
		// Poll while in flight is not needed here since beginAuth awaits.
	}

	async function runSync() {
		syncing = true;
		jobProgress = null;
		try {
			await startSync();
		} catch (e) {
			failure = e instanceof Error ? e.message : UNREADABLE_STATUS;
			syncing = false;
		}
	}

	async function cancel() {
		if (jobProgress) {
			try {
				await cancelJob(jobProgress.jid);
			} catch (e) {
				failure = e instanceof Error ? e.message : UNREADABLE_STATUS;
			}
		}
	}

	// Poll auth status while signing in.
	$effect(() => {
		if (signingIn === null) return;
		const interval = setInterval(async () => {
			try {
				status = await getAuthStatus();
			} catch {
				// ignore
			}
		}, 2000);
		return () => clearInterval(interval);
	});

	let syncState = $derived.by(() => {
		if (jobProgress && (jobProgress.state === "running" || jobProgress.state === "cancelling")) {
			return "in-progress";
		}
		if (syncLastCompletedAt) {
			const total = statusCounts ? Object.values(statusCounts).reduce((a, b) => a + b, 0) : 0;
			return total > 0 ? "synced" : "synced-empty";
		}
		if (syncStartedAt) {
			return "interrupted";
		}
		return "never";
	});
</script>

<section>
	<h1>Home</h1>

	{#if loading}
		<p class="placeholder">Checking provider sign-in…</p>
	{:else if failure !== null}
		<p class="failure" role="alert">{failure}</p>
	{:else if status !== null}
		<div class="providers">
			<ProviderRow label="Tidal" state={status.tidal} onSignIn={() => signIn("tidal")} />
			<ProviderRow label="YouTube Music" state={status.ytm} onSignIn={() => signIn("ytm")} />
		</div>

		<section class="sync">
			{#if syncState === "never"}
				<p>Never synced.</p>
			{:else if syncState === "interrupted"}
				<p>Interrupted. Run sync again?</p>
			{:else if syncState === "synced"}
				<p>Synced.</p>
			{:else if syncState === "synced-empty"}
				<p>Synced (empty library).</p>
			{/if}

			{#if jobProgress && (jobProgress.state === "running" || jobProgress.state === "cancelling")}
				<JobProgress progress={jobProgress} onCancel={cancel} />
			{/if}

			{#if !syncing}
				<button type="button" onclick={runSync}>Sync</button>
			{/if}
		</section>
	{/if}
</section>

<style>
	h1 {
		font-size: 18px;
		font-weight: 600;
		margin: 0 0 var(--space-3);
	}
	.placeholder {
		color: var(--text-2);
	}
	.failure {
		color: var(--bad);
	}
	.providers {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.sync {
		margin-top: var(--space-4);
	}
</style>
