<script lang="ts">
	import { planSegments, planTotal } from "../plan-status";

	interface Props {
		counts: Record<string, number>;
	}

	let { counts }: Props = $props();

	let segments = $derived(planSegments(counts));
	let total = $derived(planTotal(counts));
</script>

{#if total > 0}
	<div class="flex h-2.5 w-full overflow-hidden rounded-full border border-line">
		{#each segments as segment (segment.key)}
			<!--
				The bar is a shape, not information: the counts beside it are the
				readable form, and a row of coloured blocks tells a screen reader
				nothing it can use. The width is the status' share of the plan.
			-->
			<div
				data-testid="segment"
				data-status={segment.key}
				aria-hidden="true"
				class={`min-w-1 ${segment.fill}`}
				style:width={`${(segment.count / total) * 100}%`}
			></div>
		{/each}
	</div>
{/if}
