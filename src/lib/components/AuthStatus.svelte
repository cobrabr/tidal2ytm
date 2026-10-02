<script lang="ts">
	import type { ProviderState } from "../types";

	interface Props {
		label: string;
		state: ProviderState;
	}

	let { label, state }: Props = $props();
</script>

<div class="row">
	<span class="label">{label}</span>
	<!--
		`state.valid` is the only thing that decides this. `expires_at` is present
		for a cached token whether or not that token still works, so branching on
		its presence would report an expired session as a working one.
	-->
	<span class="state" class:ok={state.valid} class:bad={!state.valid}>
		{state.valid ? "Signed in" : "Not signed in"}
	</span>
	{#if state.valid && state.expires_at}
		<span class="expiry">{state.expires_at}</span>
	{/if}
</div>

<style>
	.row {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
	}
	.label {
		color: var(--text-1);
		min-width: 10rem;
	}
	.state.ok {
		color: var(--ok);
	}
	.state.bad {
		color: var(--bad);
	}
	.expiry {
		color: var(--text-2);
		font-family: var(--font-mono);
		font-size: 12px;
	}
</style>
