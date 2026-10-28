<script lang="ts">
	import type { ProviderState } from "../types";

	interface Props {
		label: string;
		state: ProviderState;
		onSignIn?: () => void;
		// A sign-in is already in flight: every row waits for the one that is
		// running, because the flow it starts is a browser and a person, and two
		// of them at once race over one token file.
		busy?: boolean;
	}

	let { label, state, onSignIn, busy = false }: Props = $props();
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
		<!-- Read off the state the row was handed: a provider with no client
		     secret cannot sign in, and a caller must not be able to offer one
		     that would only come back as a bad_request. -->
		<button type="button" disabled={!state.has_client_secret || busy} onclick={() => onSignIn?.()}> Sign in </button>
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
