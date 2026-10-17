<script lang="ts">
	import type { ProviderState } from "../types";

	interface Props {
		label: string;
		state: ProviderState;
	}

	let { label, state }: Props = $props();
</script>

<div class="row" role="group" aria-label={label}>
	<span class="label">{label}</span>
	<!--
		`state.status` is the only thing that decides this. `expires_at` is present
		for a cached token whether or not that token still works, so branching on
		its presence would report an expired session as a working one.

		Three states, because a token that expired an hour ago is not the same as
		one whose refresh token has gone too: the first is signed in and will
		refresh itself on the next call, and telling that person they are signed out
		sends them to re-authenticate over credentials that still work.

		`data-state` is the outcome as an attribute — so what is rendered can be read
		without depending on the wording above it — and it carries all three states
		rather than the signed-in/signed-out pair the boolean gave: the wording can be
		reworded, this cannot.

		`role="status"` is the live region the async load updates, and the row is a
		labelled `role="group"` because the live region announces its own text and
		nothing else: two identical cards would tell a screen-reader user that one
		provider is signed out without saying which, which is the one wrong answer a
		re-authentication cannot be aimed at.
	-->
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
