<script lang="ts">
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
			<!-- No denominator exists before the walk ends: Tidal's favourites
			     endpoint reports no count, and the count it can report includes
			     tracks it will not hand back. The count beside this is the
			     honest measure, so the spinner claims nothing it cannot know. -->
			<span class="spinner" aria-hidden="true"></span>
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
	.spinner {
		width: 14px;
		height: 14px;
		border-radius: 50%;
		border: 2px solid var(--ok);
		border-right-color: transparent;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.spinner {
			animation: none;
			border-right-color: var(--ok);
		}
	}
</style>
