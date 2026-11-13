<script lang="ts">
	import { onMount } from "svelte";
	import { Button } from "$lib/components/ui/button";
	import * as Select from "$lib/components/ui/select";
	import Panel from "../lib/components/Panel.svelte";
	import ProviderAuthRow from "../lib/components/ProviderAuthRow.svelte";
	import {
		UNREADABLE_STATUS,
		getAppInfo,
		getAuthStatus,
		getLogFiles,
		getSchemaVersion,
		getStatusCounts,
		setLogLevel,
	} from "../lib/ipc";
	import { importClientSecretFile, startSignIn } from "../lib/auth-actions";
	import { formatBytes } from "../lib/format";
	import { planTotal } from "../lib/plan-status";
	import { providerDetail } from "../lib/provider-state";
	import { uiLogFile } from "../lib/ui-log";
	import { openPath } from "@tauri-apps/plugin-opener";
	import type { AuthStatus as AuthStatusData, LogFile } from "../lib/types";

	/**
	 * The levels the host accepts, quietest first.
	 *
	 * The value is what goes over the wire and the label is what a person
	 * reads. One list owns both, so the screen cannot offer a level the host
	 * would refuse.
	 */
	const LEVELS = [
		{ value: "OFF", label: "Off" },
		{ value: "ERROR", label: "Error" },
		{ value: "WARN", label: "Warn" },
		{ value: "INFO", label: "Info" },
		{ value: "DEBUG", label: "Debug" },
	];

	let status = $state<AuthStatusData | null>(null);
	let loadFailure = $state<string | null>(null);
	let actionError = $state<string | null>(null);
	let loading = $state(true);
	let signingIn = $state<string | null>(null);

	let logLevel = $state("INFO");
	let schemaVersion = $state<number | null>(null);
	let statusCounts = $state<Record<string, number> | null>(null);
	let databasePath = $state<string | null>(null);
	let hostLogs = $state<LogFile[]>([]);
	let uiLog = $state<{ path: string; bytes: number } | null>(null);

	/** Every log the panel lists: the host's own files and the frontend's. */
	let logs = $derived<LogFile[]>([
		...hostLogs,
		...(uiLog ? [{ path: uiLog.path, bytes: uiLog.bytes, is_dir: false }] : []),
	]);

	/** How many tracks the plan holds, whatever statuses they are in. */
	let trackCount = $derived(statusCounts === null ? null : planTotal(statusCounts));

	onMount(async () => {
		try {
			status = await getAuthStatus();
		} catch (e) {
			loadFailure = e instanceof Error ? e.message : UNREADABLE_STATUS;
		} finally {
			loading = false;
		}

		try {
			databasePath = (await getAppInfo()).database_path;
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

		try {
			hostLogs = await getLogFiles();
		} catch {
			// ignore
		}

		try {
			uiLog = await uiLogFile();
		} catch {
			// ignore
		}
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

	async function changeLevel(level: string) {
		try {
			await setLogLevel(level);
			actionError = null;
		} catch (e) {
			reportAction(e);
		}
	}

	/**
	 * Open one log file.
	 *
	 * The path may sit outside the scope the opener is allowed — in a
	 * development build the log directory is outside the application data
	 * directory — so the refusal is reported rather than swallowed. A button
	 * that does nothing and says nothing is the worst of the three outcomes.
	 */
	async function openLog(path: string) {
		try {
			await openPath(path);
			actionError = null;
		} catch (e) {
			reportAction(e);
		}
	}
</script>

<section class="flex flex-col gap-4">
	<h1 class="text-lg font-semibold">Diagnostics</h1>

	{#if loading}
		<p class="text-text-faint">Loading…</p>
	{:else if loadFailure !== null}
		<p class="text-state-bad" role="alert">{loadFailure}</p>
	{:else if status !== null}
		{#if actionError !== null}
			<p class="text-state-bad" role="alert">{actionError}</p>
		{/if}

		<Panel label="Authentication">
			<div class="flex flex-col divide-y divide-surface-subtle">
				<ProviderAuthRow
					label="Tidal"
					state={status.tidal}
					detail={providerDetail(status.tidal)}
					busy={signingIn !== null}
					onAction={() => signIn("tidal")}
				/>
				<ProviderAuthRow
					label="YouTube Music"
					state={status.ytm}
					detail={providerDetail(status.ytm)}
					busy={signingIn !== null}
					disabled={!status.ytm.has_client_secret}
					onAction={() => signIn("ytm")}
				/>
			</div>
			{#if !status.ytm.has_client_secret}
				<div class="pt-3">
					<Button variant="ghost" disabled={signingIn !== null} onclick={importSecret}>Import client secret</Button>
				</div>
			{/if}
		</Panel>

		<Panel label="Log level">
			<div class="flex flex-col items-start gap-2">
				<Select.Root type="single" items={LEVELS} bind:value={logLevel} onValueChange={changeLevel}>
					<Select.Trigger>
						<Select.Value />
					</Select.Trigger>
					<Select.Content>
						{#each LEVELS as level (level.value)}
							<Select.Item value={level.value} label={level.label}>{level.label}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
				<p class="text-text-faint text-xs">The sidecar picks up the new level on its next start.</p>
			</div>
		</Panel>

		<Panel label="Storage">
			<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
				<span class="text-text-primary">Library database</span>
				{#if databasePath !== null}
					<span class="text-text-muted font-mono text-xs break-all">{databasePath}</span>
				{/if}
				<span class="ml-auto flex items-baseline gap-3 text-text-muted text-xs">
					{#if schemaVersion !== null}
						<span>schema <span class="font-mono tabular-nums">{schemaVersion}</span></span>
					{/if}
					{#if trackCount !== null}
						<span><span class="tabular-nums">{trackCount}</span> tracks</span>
					{/if}
				</span>
			</div>
		</Panel>

		<Panel label="Logs">
			<div class="flex flex-col divide-y divide-surface-subtle">
				{#each logs as log (log.path)}
					<div class="flex items-center gap-3 py-1.5">
						<span class="text-text-muted font-mono text-xs break-all">{log.path}</span>
						<span class="ml-auto flex items-center gap-3">
							{#if !log.is_dir}
								<span class="text-text-muted font-mono text-xs tabular-nums">{formatBytes(log.bytes)}</span>
							{/if}
							<Button variant="ghost" onclick={() => openLog(log.path)}>Open</Button>
						</span>
					</div>
				{/each}
			</div>
		</Panel>
	{/if}
</section>
