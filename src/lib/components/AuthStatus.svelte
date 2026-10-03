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

		`role="status"` is the live region the async load in Task 9 will update, and
		`data-state` is the outcome as an attribute — so what is rendered can be read
		without depending on the wording above it.
	-->
	<span class="state" role="status" data-state={state.valid ? "signed-in" : "signed-out"}>
		{state.valid ? "Signed in" : "Not signed in"}
	</span>
	{#if state.valid && state.expires_at}
		<time class="expiry" datetime={state.expires_at}>{state.expires_at}</time>
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
	.state[data-state="signed-in"] {
		color: var(--ok);
	}
	.state[data-state="signed-out"] {
		color: var(--bad);
	}
	.expiry {
		color: var(--text-2);
		font-family: var(--font-mono);
		font-size: 12px;
	}
</style>
