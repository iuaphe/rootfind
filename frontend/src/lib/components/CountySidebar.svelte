<script lang="ts">
	import { onMount } from 'svelte';
	import type { CountyProperties } from '../types/counties';
	import type { PlantDetails } from '../plant-details';
	import { COUNTY_PAGE_SIZE, getCountyPlantIds } from '../plant-search.js';

	let {
		county,
		query,
		matchCount,
		searching,
		searchError,
		getPlantDetails,
		onclose
	}: {
		county: CountyProperties;
		query: string | null;
		matchCount: number;
		searching: boolean;
		searchError: string;
		getPlantDetails: (id: number, signal: AbortSignal) => Promise<PlantDetails>;
		onclose: () => void;
	} = $props();

	let plants = $state<PlantDetails[]>([]);
	let loading = $state(false);
	let error = $state('');
	let pendingIds = $state<number[]>([]);
	let nextPage = 1;
	let hasMore = $state(true);
	const controller = new AbortController();

	onMount(() => {
		if (query !== null && matchCount > 0) void load();
		return () => controller.abort();
	});

	async function load() {
		if (loading || query === null || controller.signal.aborted) return;
		const { signal } = controller;
		error = '';
		loading = true;
		try {
			if (!pendingIds.length) {
				const ids = await getCountyPlantIds(query, county.geoid, nextPage, signal);
				if (signal.aborted) return;
				pendingIds = ids.filter((id) => !plants.some((plant) => plant.plant_id === id));
				nextPage++;
				hasMore = ids.length === COUNTY_PAGE_SIZE && plants.length + pendingIds.length < matchCount;
			}
			while (pendingIds.length) {
				const plant = await getPlantDetails(pendingIds[0], signal);
				if (signal.aborted) return;
				plants.push(plant);
				pendingIds = pendingIds.slice(1);
			}
		} catch (cause) {
			if (!signal.aborted)
				error =
					cause instanceof Error ? cause.message : 'Unable to load plant details. Please retry.';
		} finally {
			if (!signal.aborted) loading = false;
		}
	}

	function measurement(min: number | null | undefined, max: number | null | undefined) {
		if (min == null && max == null) return 'Unknown';
		if (min == null) return `Up to ${max} in`;
		if (max == null) return `At least ${min} in`;
		return min === max ? `${min} in` : `${min}–${max} in`;
	}

	function trait(value: boolean | null | undefined) {
		return value == null ? 'Unknown' : value ? 'Yes' : 'No';
	}

	function waterLevels(value: PlantDetails['water']) {
		const levels = Array.isArray(value) ? value : value ? [value] : [];
		return levels.map((level) => (level === 'med' ? 'medium' : level)).join(', ') || 'Unknown';
	}

	function imageUrl(value: string | null | undefined) {
		if (!value) return null;
		try {
			const url = new URL(value);
			return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
		} catch {
			return null;
		}
	}
</script>

<aside aria-labelledby="county-heading">
	<header>
		<div>
			<p class="eyebrow">{county.state_name}</p>
			<h2 id="county-heading">{county.name}</h2>
		</div>
		<button
			type="button"
			class="close"
			aria-label="Close county details"
			onclick={onclose}
			{@attach (element) => element.focus()}>×</button
		>
	</header>
	<div class="content">
		{#if searching}
			<p role="status">Searching for matching species…</p>
		{:else if searchError}
			<p role="status">Run a successful search to see matching species in this county.</p>
		{:else if query === null}
			<p role="status">Search for plants to see matching species in this county.</p>
		{:else}
			<p class="summary">{matchCount.toLocaleString()} matching species in this county</p>
			{#if matchCount === 0}
				<p role="status">
					No species match your search and filters in this county. Try another search or adjust the
					filters.
				</p>
			{/if}
			{#if loading}
				<p role="status">
					Loading matching plants… {plants.length.toLocaleString()} of {matchCount.toLocaleString()}
				</p>
			{/if}
			{#if error}
				<div class="error" role="alert">
					<p>{error}</p>
					<p>
						Showing {plants.length.toLocaleString()} of {matchCount.toLocaleString()} species.
					</p>
					<button type="button" onclick={() => void load()}>Retry loading plants</button>
				</div>
			{/if}
			<ul aria-label="Matching species">
				{#each plants as plant (plant.plant_id)}
					{@const scientificName = `${plant.genus} ${plant.species}`}
					{@const image = imageUrl(plant.image_link)}
					<li>
						<article>
							{#if image}
								<img
									src={image}
									alt={scientificName}
									loading="lazy"
									onerror={(event) => {
										if (event.currentTarget instanceof HTMLImageElement)
											event.currentTarget.hidden = true;
									}}
								/>
							{/if}
							<h3><i>{scientificName}</i></h3>
							{#if plant.common_names.length}<p class="names">
									{plant.common_names.join(', ')}
								</p>{/if}
							<dl>
								<div>
									<dt>Life cycle</dt>
									<dd>{plant.life_cycle ?? 'Unknown'}</dd>
								</div>
								<div>
									<dt>Foliage</dt>
									<dd>{plant.foliage ?? 'Unknown'}</dd>
								</div>
								<div>
									<dt>Sun</dt>
									<dd>{plant.sun ?? 'Unknown'}</dd>
								</div>
								<div>
									<dt>Water</dt>
									<dd>{waterLevels(plant.water)}</dd>
								</div>
								<div>
									<dt>Height</dt>
									<dd>{measurement(plant.minheight, plant.maxheight)}</dd>
								</div>
								<div>
									<dt>Width</dt>
									<dd>{measurement(plant.minwidth, plant.maxwidth)}</dd>
								</div>
								<div>
									<dt>Woody</dt>
									<dd>{trait(plant.is_woody)}</dd>
								</div>
								<div>
									<dt>Tree</dt>
									<dd>{trait(plant.is_tree)}</dd>
								</div>
								<div>
									<dt>Edible</dt>
									<dd>{trait(plant.edible)}</dd>
								</div>
								<div>
									<dt>Thorns</dt>
									<dd>{trait(plant.thorns)}</dd>
								</div>
							</dl>
							{#if plant.misc?.length}<p class="notes">{plant.misc.join(' · ')}</p>{/if}
						</article>
					</li>
				{/each}
			</ul>
			{#if !loading && !error && plants.length > 0}
				<p class="page-status">
					Showing {plants.length.toLocaleString()} of {matchCount.toLocaleString()} species.
				</p>
				{#if hasMore}
					<button type="button" onclick={() => void load()}>Load more species</button>
				{/if}
			{:else if !loading && !error && matchCount > 0}
				<p role="status">These results have changed. Search again to refresh the county counts.</p>
			{/if}
		{/if}
	</div>
</aside>

<style>
	aside {
		display: flex;
		flex-direction: column;
		width: min(420px, 45vw);
		min-width: 0;
		min-height: 0;
		background: var(--surface-panel);
		color: var(--text);
		border-left: 1px solid var(--border);
	}
	header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 16px;
		padding: 20px 24px;
		border-bottom: 1px solid var(--border);
	}
	.eyebrow {
		margin: 0 0 4px;
		font-size: 0.75rem;
		color: var(--muted);
	}
	h2 {
		font-size: 1.25rem;
		font-weight: 600;
	}
	button {
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 8px 12px;
		cursor: pointer;
		background: var(--surface-raised);
		color: var(--text);
	}
	.close {
		border: 0;
		font-size: 1.5rem;
		line-height: 1;
	}
	button:hover {
		background: var(--surface-hover);
	}
	button:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
	}
	.content {
		overflow-y: auto;
		overscroll-behavior: contain;
		padding: 20px 24px;
	}
	p {
		font-size: 0.875rem;
		line-height: 1.5;
	}
	.summary {
		font-weight: 600;
		margin-bottom: 12px;
	}
	.page-status {
		margin: 16px 0 12px;
	}
	ul {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	li {
		padding: 20px 0;
		border-bottom: 1px solid var(--border);
	}
	h3 {
		font-size: 1.0625rem;
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.names {
		margin-top: 4px;
		color: var(--muted);
	}
	img {
		width: 100%;
		height: 170px;
		object-fit: cover;
		border-radius: 8px;
		margin-bottom: 12px;
	}
	dl {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px 16px;
		margin-top: 16px;
		font-size: 0.8125rem;
	}
	dt {
		color: var(--muted);
	}
	dd {
		margin-top: 2px;
		text-transform: capitalize;
		overflow-wrap: anywhere;
	}
	.notes {
		margin-top: 16px;
	}
	.error {
		padding: 12px;
		border: 1px solid #653434;
		border-radius: 6px;
		color: var(--error);
	}
	.error p {
		margin-bottom: 8px;
	}
	@media (max-width: 600px) {
		aside {
			position: absolute;
			z-index: 1;
			inset: auto 0 0;
			width: 100%;
			max-height: 60%;
			border-left: 0;
			border-top: 1px solid var(--border);
			box-shadow: 0 -4px 16px #0008;
		}
		header {
			padding: 16px;
		}
		.content {
			padding: 16px;
		}
	}
</style>
