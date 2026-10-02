<script lang="ts">
	import AuthStatus from "../lib/components/AuthStatus.svelte";
	import type { AuthStatus as AuthStatusData } from "../lib/types";

	// Task 9 replaces this with a real fetch. Until then the shell must render
	// something honest rather than a fabricated "signed in". `$state` rather
	// than a plain `let` so the type stays `AuthStatusData | null`: TypeScript
	// narrows a plain `let` that is never assigned to its initial `null`, which
	// collapses the populated branch to `never`.
	let status = $state<AuthStatusData | null>(null);
</script>

<section>
	<h1>Home</h1>
	{#if status === null}
		<p class="placeholder">Provider status loads here.</p>
	{:else}
		<AuthStatus label="Tidal" state={status.tidal} />
		<AuthStatus label="YouTube Music" state={status.ytm} />
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
</style>
