<script lang="ts">
	import { Spinner } from "$lib/components/ui/spinner";
	import type { Progress } from "../jobs";

	interface Props {
		progress: Progress;
		onCancel?: () => void;
	}

	let { progress, onCancel }: Props = $props();
</script>

<div class="job" role="status" data-state={progress.state}>
	<div class="line">
		{#if progress.state === "running" || progress.state === "cancelling"}
			<!-- Decoration: the row is already a live region, so the icon stays
			     out of the accessibility tree. -->
			<Spinner aria-hidden="true" />
		{/if}
		<span
			class="state"
			style:color={progress.state === "running" || progress.state === "done"
				? "var(--ok)"
				: progress.state === "cancelling"
					? "var(--warn)"
					: "var(--bad)"}>{progress.state}</span
		>
		<span class="count"
			>{progress.done}{#if progress.total !== null}
				/ {progress.total}{/if}</span
		>
		{#if progress.state === "running"}
			<button type="button" onclick={() => onCancel?.()}>stop</button>
		{:else if progress.state === "cancelling"}
			<button type="button" disabled>stopping</button>
		{/if}
	</div>
	{#if progress.total !== null && (progress.state === "running" || progress.state === "cancelling" || progress.state === "done")}
		<!-- No denominator exists before the walk ends: Tidal's favourites
		     endpoint reports no count, and the count it can report includes
		     tracks it will not hand back. The count in the row is the
		     honest measure. -->
		<progress aria-label="job progress" value={progress.done} max={progress.total}></progress>
	{/if}
</div>

<style>
	.job {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	.line {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
	}
	progress {
		width: 100%;
		height: 6px;
	}
</style>
