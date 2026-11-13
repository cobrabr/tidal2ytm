<script lang="ts">
	import { Badge } from "$lib/components/ui/badge";
	import { providerPillLabel, providerVariant } from "../provider-state";
	import type { ProviderState } from "../types";

	interface Props {
		state: ProviderState;
	}

	let { state }: Props = $props();
</script>

<!--
	The pill takes the wire's shape and narrows it itself, so a status this
	build does not know reads as signed out instead of breaking the row, and
	no call site has to repeat the narrowing. The text is the meaning; the
	variant only says the same thing a second time, so the pill is readable
	in greyscale and in a screen reader.
-->
<Badge variant={providerVariant(state)}>{providerPillLabel(state)}</Badge>
