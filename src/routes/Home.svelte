<script lang="ts">
	import { onMount } from "svelte";
	import AuthStatus from "../lib/components/AuthStatus.svelte";
	import { UNREADABLE_STATUS, getAuthStatus } from "../lib/ipc";
	import type { AuthStatus as AuthStatusData } from "../lib/types";

	// Assigned by `onMount`, and that assignment is load-bearing in a way a type
	// cannot express. `$state` initialised to `null` and never assigned is a
	// non-reactive constant to the compiler, so `status !== null` folds to
	// `false`, the populated branch is dropped from the bundle — scoped CSS and
	// all — and `pnpm build` still succeeds with no `data-state` anywhere in
	// `dist/`. `pnpm check` is satisfied either way and the component's own
	// tests render it directly, so nothing else notices.
	let status = $state<AuthStatusData | null>(null);
	let failure = $state<string | null>(null);
	let loading = $state(true);

	onMount(async () => {
		try {
			status = await getAuthStatus();
		} catch (e) {
			// `getAuthStatus` normalises the boundary's rejection into an `Error`,
			// so the first arm is the one that runs. The second is unreachable
			// through it and is here because `catch` binds `unknown` — dropping the
			// guard would only move the mistake to a screen that forgets it.
			failure = e instanceof Error ? e.message : UNREADABLE_STATUS;
		} finally {
			loading = false;
		}
	});
</script>

<section>
	<h1>Home</h1>

	<!--
		Three states, not two. "Not loaded yet", "loaded and both signed out", and
		"the sidecar is not running" look identical if they collapse into a single
		empty branch, and the fix for each is different.
	-->
	{#if loading}
		<p class="placeholder">Checking provider sign-in…</p>
	{:else if failure !== null}
		<p class="failure" role="alert">{failure}</p>
	{:else if status !== null}
		<div class="providers">
			<AuthStatus label="Tidal" state={status.tidal} />
			<AuthStatus label="YouTube Music" state={status.ytm} />
		</div>
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
</style>
