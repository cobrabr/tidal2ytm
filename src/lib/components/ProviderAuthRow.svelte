<script lang="ts">
	import { Button } from "$lib/components/ui/button";
	import StatePill from "./StatePill.svelte";
	import { providerDotClass, providerStateAttribute, providerStatus } from "../provider-state";
	import type { ProviderState } from "../types";

	interface Props {
		label: string;
		state: ProviderState;
		detail: string;
		busy: boolean;
		disabled?: boolean;
		onAction: () => void;
	}

	let { label, state, detail, busy, disabled = false, onAction }: Props = $props();
</script>

<!--
	One provider's state and the one action that changes it.

	The dot is the glance and the pill is the reading, and both say the same
	thing: the colour is in two places, never in one alone. The pill carries the
	words, so the row still reads in greyscale and in a screen reader.

	The button is the panel's primary action only while the provider is signed
	out — the one state where re-authenticating is what the panel is for. Signed
	in, it is a quiet ghost button beside the state it would disturb.
-->
<div class="flex items-center gap-2.5 py-1.5" role="group" aria-label={label}>
	<span class={`size-1.75 shrink-0 rounded-full ${providerDotClass(state)}`} aria-hidden="true"></span>
	<span class="text-text-primary">{label}</span>
	<!--
		The live region the async sign-in updates; the group's label says which
		provider it was. The attribute carries the outcome without depending on
		the pill's wording, which can be reworded without breaking a reader.
	-->
	<span role="status" data-state={providerStateAttribute(state)}><StatePill {state} /></span>
	<span class="text-text-muted text-xs">{detail}</span>
	<span class="ml-auto">
		<Button
			variant={providerStatus(state) === "signed_out" ? "default" : "ghost"}
			disabled={busy || disabled}
			onclick={onAction}
		>
			Re-authenticate
		</Button>
	</span>
</div>
