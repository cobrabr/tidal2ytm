<script lang="ts">
	/**
	 * The order a track moves through, fixed rather than sorted by count: rows
	 * that swap places as a transfer runs are unreadable at the moment the
	 * numbers finally matter.
	 */
	const ORDER = ["pending", "transferred", "skip", "failed", "needs_review"] as const;

	const LABELS: Record<string, string> = {
		pending: "Pending",
		transferred: "Transferred",
		skip: "Skipped",
		failed: "Failed",
		needs_review: "Needs review",
	};

	interface Props {
		counts: Record<string, number>;
	}

	let { counts }: Props = $props();

	let rows = $derived.by(() => {
		const known: string[] = ORDER.filter((key) => (counts[key] ?? 0) > 0);
		// A status this build does not know is still a row of the plan: hiding
		// it would make the total disagree with the database it was read from.
		const unknown = Object.keys(counts)
			.filter((key) => !known.includes(key) && !(ORDER as readonly string[]).includes(key))
			.filter((key) => (counts[key] ?? 0) > 0)
			.sort();
		return [...known, ...unknown].map((key) => ({
			key,
			label: LABELS[key] ?? key,
			count: counts[key] ?? 0,
		}));
	});

	let total = $derived(Object.values(counts).reduce((sum, value) => sum + value, 0));
</script>

{#if rows.length === 0}
	<p class="empty">No tracks yet.</p>
{:else}
	<dl>
		{#each rows as row (row.key)}
			<div class="row">
				<dt>{row.label}</dt>
				<dd>{row.count}</dd>
			</div>
		{/each}
		<div class="row total">
			<dt>Total</dt>
			<dd>{total}</dd>
		</div>
	</dl>
{/if}

<style>
	dl {
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	.row {
		display: flex;
		justify-content: space-between;
		gap: var(--space-4);
	}
	dt {
		color: var(--text-2);
	}
	dd {
		margin: 0;
		font-variant-numeric: tabular-nums;
	}
	.total {
		font-weight: 600;
	}
	.empty {
		color: var(--text-2);
	}
</style>
