<script lang="ts">
	import { onMount, onDestroy } from "svelte";
	import ProviderRow from "../lib/components/ProviderRow.svelte";
	import JobProgress from "../lib/components/JobProgress.svelte";
	import { UNREADABLE_STATUS, getAuthStatus, getStatusCounts, getMeta, startSync, cancelJob } from "../lib/ipc";
	import { importClientSecretFile, startSignIn } from "../lib/auth-actions";
	import { subscribeJobs, type Progress } from "../lib/jobs";
	import type { AuthStatus as AuthStatusData } from "../lib/types";

	let status = $state<AuthStatusData | null>(null);
	let loadFailure = $state<string | null>(null);
	let actionError = $state<string | null>(null);
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
			loadFailure = e instanceof Error ? e.message : UNREADABLE_STATUS;
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

	/** Show an action's failure beside the action, keeping the screen usable. */
	function reportAction(e: unknown) {
		actionError = e instanceof Error ? e.message : UNREADABLE_STATUS;
	}

	async function signIn(provider: string) {
		signingIn = provider;
		try {
			status = await startSignIn(provider);
			actionError = null;
		} catch (e) {
			reportAction(e);
		} finally {
			signingIn = null;
		}
	}

	async function importSecret() {
		try {
			const next = await importClientSecretFile();
			if (next !== null) {
				status = next;
				actionError = null;
			}
		} catch (e) {
			reportAction(e);
		}
	}

	async function runSync() {
		syncing = true;
		jobProgress = null;
		try {
			await startSync();
			actionError = null;
		} catch (e) {
			reportAction(e);
			syncing = false;
		}
	}

	async function cancel() {
		if (jobProgress) {
			try {
				await cancelJob(jobProgress.jid);
			} catch (e) {
				reportAction(e);
			}
		}
	}

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
	{:else if loadFailure !== null}
		<p class="failure" role="alert">{loadFailure}</p>
	{:else if status !== null}
		{#if actionError !== null}
			<p class="failure" role="alert">{actionError}</p>
		{/if}
		<div class="providers">
			<ProviderRow label="Tidal" state={status.tidal} busy={signingIn !== null} onSignIn={() => signIn("tidal")} />
			<ProviderRow label="YouTube Music" state={status.ytm} busy={signingIn !== null} onSignIn={() => signIn("ytm")} />
			{#if !status.ytm.has_client_secret}
				<button type="button" disabled={signingIn !== null} onclick={importSecret}> Import client secret </button>
			{/if}
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
