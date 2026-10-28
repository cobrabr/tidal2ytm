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
	{#if progress.state === "running" || progress.state === "cancelling" || progress.state === "done"}
		{#if progress.total === null}
			<!-- No denominator exists before the walk ends: Tidal's favourites
			     endpoint reports no count, and the count it can report includes
			     tracks it will not hand back. A native indeterminate progress
			     element animates a segment forwards and then backwards, which
			     reads as the sync losing ground, so this pulses in place. -->
			<div class="working" aria-label="job in progress"></div>
		{:else}
			<progress aria-label="job progress" value={progress.done} max={progress.total}></progress>
		{/if}
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
	.working {
		width: 100%;
		height: 6px;
		border-radius: 3px;
		background: var(--ok);
		animation: breathe 1.4s ease-in-out infinite;
	}
	@keyframes breathe {
		0%,
		100% {
			opacity: 0.25;
		}
		50% {
			opacity: 1;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.working {
			animation: none;
			opacity: 0.6;
		}
	}
</style>
