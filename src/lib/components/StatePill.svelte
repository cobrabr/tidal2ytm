<script lang="ts">
	import { Badge } from "$lib/components/ui/badge";
	import { PROVIDER_STATES, type ProviderStatus } from "../provider-state";

	interface Props {
		state: ProviderStatus;
	}

	let { state }: Props = $props();

	/**
	 * The variant that repeats the state. The text is the meaning; the colour
	 * only says the same thing a second time, so the pill is readable in
	 * greyscale and in a screen reader.
	 */
	const VARIANTS: Record<ProviderStatus, "success" | "warning" | "danger"> = {
		valid: "success",
		refreshable: "warning",
		signed_out: "danger",
	};
</script>

<Badge variant={VARIANTS[state]}>{PROVIDER_STATES[state].label}</Badge>
