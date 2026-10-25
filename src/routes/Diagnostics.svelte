<script lang="ts">
	import { onMount } from "svelte";
	import AuthStatus from "../lib/components/AuthStatus.svelte";
	import {
		UNREADABLE_STATUS,
		getAuthStatus,
		beginAuth,
		getLogPaths,
		setLogLevel,
		importClientSecret,
		getStatusCounts,
		getSchemaVersion,
	} from "../lib/ipc";
	import { open } from "@tauri-apps/plugin-dialog";
	import { openPath } from "@tauri-apps/plugin-opener";
	import type { AuthStatus as AuthStatusData } from "../lib/types";

	let status = $state<AuthStatusData | null>(null);
	let failure = $state<string | null>(null);
	let loading = $state(true);

	let logPaths = $state<{ host_log: string; history: string; frontend_log: string } | null>(null);
	let logLevel = $state("INFO");
	let schemaVersion = $state<number | null>(null);
	let statusCounts = $state<Record<string, number> | null>(null);

	onMount(async () => {
		try {
			status = await getAuthStatus();
		} catch (e) {
			failure = e instanceof Error ? e.message : UNREADABLE_STATUS;
		} finally {
			loading = false;
		}

		try {
			logPaths = await getLogPaths();
		} catch {
			// ignore
		}

		try {
			schemaVersion = await getSchemaVersion();
		} catch {
			// ignore
		}

		try {
			statusCounts = await getStatusCounts();
		} catch {
			// ignore
		}
	});

	async function signIn(provider: string) {
		try {
			await beginAuth(provider);
			status = await getAuthStatus();
		} catch (e) {
			failure = e instanceof Error ? e.message : UNREADABLE_STATUS;
		}
	}

	async function importSecret() {
		try {
			const chosen = await open({ filters: [{ name: "JSON", extensions: ["json"] }] });
			if (typeof chosen === "string") {
				await importClientSecret(chosen);
				status = await getAuthStatus();
			}
		} catch (e) {
			failure = e instanceof Error ? e.message : UNREADABLE_STATUS;
		}
	}

	async function changeLevel(level: string) {
		logLevel = level;
		try {
			await setLogLevel(level);
		} catch (e) {
			failure = e instanceof Error ? e.message : UNREADABLE_STATUS;
		}
	}

	async function openLogs() {
		if (logPaths) {
			try {
				await openPath(logPaths.history);
			} catch {
				// ignore
			}
		}
	}
</script>

<section>
	<h1>Diagnostics</h1>

	{#if loading}
		<p class="placeholder">Loading…</p>
	{:else if failure !== null}
		<p class="failure" role="alert">{failure}</p>
	{:else if status !== null}
		<!-- 1. Sign-in and client secret -->
		<section class="block">
			<h2>Sign-in</h2>
			<div class="providers">
				<AuthStatus label="Tidal" state={status.tidal} />
				<button type="button" onclick={() => signIn("tidal")}>Sign in to Tidal</button>
				<AuthStatus label="YouTube Music" state={status.ytm} />
				<button type="button" onclick={() => signIn("ytm")}>Sign in to YouTube Music</button>
				{#if !status.ytm.has_client_secret}
					<button type="button" onclick={importSecret}>Import client secret</button>
				{/if}
			</div>
		</section>

		<!-- 2. Log level -->
		<section class="block">
			<h2>Log level</h2>
			<select value={logLevel} onchange={(e) => changeLevel((e.target as HTMLSelectElement).value)}>
				<option value="OFF">Off</option>
				<option value="ERROR">Error</option>
				<option value="WARN">Warn</option>
				<option value="INFO">Info</option>
				<option value="DEBUG">Debug</option>
			</select>
			<p class="hint">The sidecar picks up the new level on its next start.</p>
		</section>

		<!-- 3. Log files -->
		<section class="block">
			<h2>Log files</h2>
			{#if logPaths}
				<ul>
					<li>{logPaths.host_log}</li>
					<li>{logPaths.history}</li>
					<li>{logPaths.frontend_log}</li>
				</ul>
			{/if}
			<button type="button" onclick={openLogs}>Open logs directory</button>
		</section>

		<!-- 4. Database statistics -->
		<section class="block">
			<h2>Database</h2>
			{#if schemaVersion !== null}
				<p>Schema version: {schemaVersion}</p>
			{/if}
			{#if statusCounts}
				<ul>
					{#each Object.entries(statusCounts) as [key, value] (key)}
						<li>{key}: {value}</li>
					{/each}
				</ul>
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
	h2 {
		font-size: 14px;
		font-weight: 600;
		margin: var(--space-3) 0 var(--space-2);
	}
	.block {
		margin-bottom: var(--space-4);
	}
	.providers {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.placeholder {
		color: var(--text-2);
	}
	.failure {
		color: var(--bad);
	}
	.hint {
		color: var(--text-2);
		font-size: 12px;
	}
	ul {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	li {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--text-1);
	}
	select {
		font: inherit;
	}
	button {
		margin-top: var(--space-1);
	}
</style>
