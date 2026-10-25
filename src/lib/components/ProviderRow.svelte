<script lang="ts">
	import type { ProviderState } from "../types";

	interface Props {
		label: string;
		state: ProviderState;
		onSignIn?: () => void;
	}

	let { label, state, onSignIn }: Props = $props();
</script>

<div class="row" role="group" aria-label={label}>
	<span class="label">{label}</span>
	<span class="state" role="status" data-state={state.status === "signed_out" ? "signed-out" : state.status}>
		{state.status === "valid"
			? "Signed in"
			: state.status === "refreshable"
				? "Signed in, will refresh"
				: "Not signed in"}
	</span>
	{#if state.status !== "signed_out" && state.expires_at}
		<time class="expiry" datetime={state.expires_at}>{state.expires_at}</time>
	{/if}
	{#if state.status === "signed_out"}
		<button type="button" onclick={() => onSignIn?.()}>Sign in</button>
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
	.state[data-state="valid"] {
		color: var(--ok);
	}
	.state[data-state="refreshable"] {
		color: var(--warn);
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
