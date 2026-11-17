<script lang="ts">
	import StatePill from "./StatePill.svelte";
	import { providerDetail, providerDotClass, providerStateAttribute } from "../provider-state";
	import type { ProviderState } from "../types";

	interface Props {
		label: string;
		state: ProviderState;
	}

	let { label, state }: Props = $props();
</script>

<!--
	Home states the provider's condition and leaves acting to Diagnostics. The
	dot is the glance and the pill is the reading; both come from one owner so
	they cannot disagree, and the pill carries the words so the row reads in
	greyscale and in a screen reader. The live region's attribute carries the
	outcome without depending on the pill's wording, which is free to change.
-->
<div class="flex items-center gap-2.5 py-1.5" role="group" aria-label={label}>
	<span class={`size-1.75 shrink-0 rounded-full ${providerDotClass(state)}`} aria-hidden="true"></span>
	<span class="text-text-primary">{label}</span>
	<span role="status" data-state={providerStateAttribute(state)}><StatePill {state} /></span>
	<span class="text-text-muted text-xs">{providerDetail(state)}</span>
</div>
