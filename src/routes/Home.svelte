<script lang="ts">
	import { onMount, onDestroy } from "svelte";
	import { Button } from "$lib/components/ui/button";
	import Panel from "../lib/components/Panel.svelte";
	import ProviderRow from "../lib/components/ProviderRow.svelte";
	import JobProgress from "../lib/components/JobProgress.svelte";
	import SegmentBar from "../lib/components/SegmentBar.svelte";
	import CountTiles from "../lib/components/CountTiles.svelte";
	import { UNREADABLE_STATUS, getAuthStatus, getStatusCounts, getMeta, startSync, cancelJob } from "../lib/ipc";
	import { subscribeJobs, type Progress } from "../lib/jobs";
	import { planTotal } from "../lib/plan-status";
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

	let unlistenJobs: (() => void) | null = null;

	/** One reading of the library, shared by the page head and the sync state. */
	let libraryCount = $derived(statusCounts === null ? 0 : planTotal(statusCounts));

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
			return libraryCount > 0 ? "synced" : "synced-empty";
		}
		if (syncStartedAt) {
			return "interrupted";
		}
		return "never";
	});
</script>

<section class="flex flex-col gap-5">
	<header class="flex items-baseline justify-between gap-4">
		<h1 class="text-lg font-semibold">Home</h1>
		{#if statusCounts !== null}
			<p class="text-text-muted text-xs">
				<span class="tabular-nums font-semibold text-text-primary">{libraryCount.toLocaleString()}</span> tracks in library
			</p>
		{/if}
	</header>

	{#if loading}
		<p class="text-text-faint">Checking provider sign-in…</p>
	{:else if loadFailure !== null}
		<p class="text-state-bad" role="alert">{loadFailure}</p>
	{:else if status !== null}
		{#if actionError !== null}
			<p class="text-state-bad" role="alert">{actionError}</p>
		{/if}

		<Panel label="Sync">
			{#if !syncing}
				<Button onclick={runSync}>Sync now</Button>
			{/if}
			{#if syncState === "never"}
				<p class="text-text-muted text-xs mt-2">Never synced</p>
			{:else if syncState === "interrupted"}
				<p class="text-text-muted text-xs mt-2">Last sync did not finish</p>
			{:else if syncState === "synced"}
				<p class="text-text-muted text-xs mt-2">Synced</p>
			{:else if syncState === "synced-empty"}
				<p class="text-text-muted text-xs mt-2">Synced (empty library)</p>
			{/if}
			{#if jobProgress !== null}
				<div class="border-line mt-3 border-t pt-3">
					<JobProgress progress={jobProgress} onCancel={cancel} />
				</div>
			{/if}
		</Panel>

		<Panel label="Transfer plan">
			{#if statusCounts}
				<SegmentBar counts={statusCounts} />
				{#if libraryCount > 0}
					<div class="mt-3">
						<CountTiles counts={statusCounts} />
					</div>
				{:else}
					<p class="text-text-muted text-xs">No tracks yet.</p>
				{/if}
			{/if}
		</Panel>

		<Panel label="Providers">
			<div class="flex flex-col divide-y divide-surface-subtle">
				<ProviderRow label="Tidal" state={status.tidal} />
				<ProviderRow label="YouTube Music" state={status.ytm} />
			</div>
		</Panel>
	{/if}
</section>
