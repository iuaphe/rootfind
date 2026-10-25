<script lang="ts">
	import { Map, type CountyColorAssignment, type CountyFeature, type CountyProperties } from '#lib';
	import { onMount } from 'svelte';
	import RangeSlider from '#lib/components/RangeSlider.svelte';
	import CountySidebar from '#lib/components/CountySidebar.svelte';
	import PlantSearchBox from '#lib/components/PlantSearchBox.svelte';
	import {
		applySearchInput,
		parseSearchInput,
		type TaxonomySpecifier
	} from '#lib/plant-search-input.js';
	import { createPlantDetailsClient } from '#lib/plant-details.js';
	import { getCountyCounts } from '#lib/plant-search.js';
	import {
		booleanFilters,
		countFilters,
		createFilters,
		enumFilters,
		filterParams,
		HEIGHT_LIMIT,
		WIDTH_LIMIT,
		type EnumFilterKey
	} from '#lib/plant-filters.js';

	const getPlantDetails = createPlantDetailsClient();
	let search = $state('');
	let taxonomy = $state<TaxonomySpecifier[]>([]);
	let loading = $state(false);
	let error = $state('');
	let message = $state('');
	let countyCounts = $state.raw<Record<number, number>>({});
	let completedSearch = $state<string | null>(null);
	let selectedCounty = $state.raw<CountyProperties | null>(null);
	let assignment: CountyColorAssignment = $state(() => `#bbb`);
	let controller: AbortController | undefined;
	let filtersOpen = $state(false);
	let filters = $state(createFilters());
	let appliedFilters = $state(createFilters());
	const activeFilters = $derived(countFilters(appliedFilters));
	const suggestionContext = $derived(filterParams(appliedFilters).toString());

	function toggleOption(key: EnumFilterKey, option: string) {
		filters[key] = filters[key].includes(option)
			? filters[key].filter((value) => value !== option)
			: [...filters[key], option];
	}

	function closeFilters() {
		filtersOpen = false;
		document.getElementById('filter-button')?.focus();
	}

	function setZoneBound(key: 'minZone' | 'maxZone', value: number | undefined) {
		filters[key] = value;
		if (
			filters.minZone !== undefined &&
			filters.maxZone !== undefined &&
			filters.minZone > filters.maxZone
		) {
			filters[key === 'minZone' ? 'maxZone' : 'minZone'] = value;
		}
	}

	function submitSearch() {
		if (loading) return;
		try {
			const parsed = parseSearchInput(search, taxonomy);
			search = parsed.text;
			taxonomy = parsed.specifiers;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Check the search query.';
			return;
		}
		if (filtersOpen) {
			appliedFilters = $state.snapshot(filters);
			closeFilters();
		}
		void searchPlants();
	}

	async function searchPlants() {
		const query = search.trim().replace(/\s+/g, ' ');
		if (loading) return;
		const params = applySearchInput(filterParams(appliedFilters), query, taxonomy);
		const description =
			[
				...taxonomy.map(({ field, value }) => `${field}=${value}`),
				...(query ? [`“${query}”`] : [])
			].join(', ') || 'all plants';

		controller?.abort();
		controller = new AbortController();
		const { signal } = controller;
		loading = true;
		error = '';
		message = `Searching ${description}…`;
		completedSearch = null;
		countyCounts = {};
		assignment = () => '#fff';

		try {
			const counts = await getCountyCounts(params, signal);
			if (signal.aborted) return;
			countyCounts = counts;
			completedSearch = params.toString();
			const values = Object.values(counts);
			const max = Math.max(1, ...values);
			assignment = (c: CountyProperties) => {
				// function hsvToRgb(h: number, s: number, v: number) {
				// 	s /= 100;
				// 	v /= 100;

				// 	const k = (n: number) => (n + h / 60) % 6;
				// 	const f = (n: number) => v - v * s * Math.max(0, Math.min(k(n), 4 - k(n), 1));

				// 	return [Math.round(255 * f(5)), Math.round(255 * f(3)), Math.round(255 * f(1))];
				// }
				const num = counts[parseInt(c.geoid, 10)] ?? 0;
				// const hue = 360 * (num / max);
				// const [r, g, b] = hsvToRgb(hue, 30, 100);
				const greenness = 255 * (1 - num / max);
				return `rgb(${greenness}, 255, ${greenness})`;
			};
			const matchedCounties = values.filter((count) => count > 0).length;
			message = matchedCounties
				? `Matches in ${matchedCounties.toLocaleString()} ${matchedCounties === 1 ? 'county' : 'counties'}. Click a county to see its plants.`
				: `No plants with county records found for ${description}. Try another name or adjust the filters.`;
		} catch (cause) {
			if (!signal.aborted) {
				message = '';
				error =
					cause instanceof Error ? cause.message : 'Unable to search plants. Please try again.';
			}
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void searchPlants();
		return () => controller?.abort();
	});

	const onClick = (c: CountyFeature) => {
		selectedCounty = c.properties;
	};

	function closeCounty() {
		selectedCounty = null;
		document.querySelector<HTMLCanvasElement>('.map-area canvas')?.focus();
	}
</script>

<svelte:window
	onkeydown={(event) => {
		if (event.key === 'Escape') {
			if (filtersOpen) closeFilters();
			else if (selectedCounty) closeCounty();
		}
	}}
/>

<main>
	<section class="search-panel" aria-label="Plant search">
		<form
			role="search"
			onsubmit={(event) => {
				event.preventDefault();
				submitSearch();
			}}
		>
			<label for="plant-search">Search for a plant</label>
			<div class="search-controls">
				<PlantSearchBox bind:text={search} bind:specifiers={taxonomy} context={suggestionContext} />
				<button class="primary" type="submit" disabled={loading}>
					{loading ? 'Searching…' : 'Search'}
				</button>
				<button
					id="filter-button"
					type="button"
					class={['filter-toggle', { active: filtersOpen || activeFilters > 0 }]}
					aria-expanded={filtersOpen}
					aria-controls="plant-filters"
					onclick={() => {
						if (!filtersOpen) filters = $state.snapshot(appliedFilters);
						filtersOpen = !filtersOpen;
					}}
				>
					<svg
						width="18"
						height="18"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="1.7"
						aria-hidden="true"
					>
						<path d="M4 7h9m4 0h3M4 17h3m4 0h9" />
						<circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" />
					</svg>
					Filters
					{#if activeFilters > 0}<span class="filter-count">{activeFilters}</span>{/if}
				</button>
			</div>
			<p id="search-help" class="search-help">
				Add taxonomy filters with a comma, e.g. family=Aceraceae. Enter accepts a suggestion.
			</p>
			{#if filtersOpen}
				<section id="plant-filters" class="filter-menu" aria-labelledby="filter-heading">
					<div class="filter-header">
						<div>
							<h2 id="filter-heading">Filter plants</h2>
							<p>Choose the conditions and traits you’re looking for.</p>
						</div>
						<button
							type="button"
							class="close-button"
							aria-label="Close filters"
							onclick={closeFilters}>×</button
						>
					</div>
					<div class="filter-grid">
						<fieldset class="zones">
							<legend>Hardiness zones</legend>
							<div class="zone-range">
								<div>
									<label for="min-zone">Minimum zone</label>
									<select
										id="min-zone"
										bind:value={() => filters.minZone, (value) => setZoneBound('minZone', value)}
									>
										<option value={undefined}>Any minimum</option>
										{#each Array.from({ length: 13 }, (_, i) => i + 1) as zone (zone)}
											<option value={zone}>{zone}</option>
										{/each}
									</select>
								</div>
								<div>
									<label for="max-zone">Maximum zone</label>
									<select
										id="max-zone"
										bind:value={() => filters.maxZone, (value) => setZoneBound('maxZone', value)}
									>
										<option value={undefined}>Any maximum</option>
										{#each Array.from({ length: 13 }, (_, i) => i + 1) as zone (zone)}
											<option value={zone}>{zone}</option>
										{/each}
									</select>
								</div>
							</div>
							<p>Match plants whose recorded zones overlap this range.</p>
						</fieldset>
						{#each enumFilters as { key, label, options } (key)}
							<fieldset>
								<legend>{label}</legend>
								<div class="options">
									{#each options as option (option)}
										<button
											type="button"
											class="option"
											aria-pressed={filters[key].includes(option)}
											onclick={() => toggleOption(key, option)}
											>{option === 'med' ? 'Medium' : option}</button
										>
									{/each}
								</div>
							</fieldset>
						{/each}
						<RangeSlider
							label="Height"
							limit={HEIGHT_LIMIT}
							bind:lower={filters.minHeight}
							bind:upper={filters.maxHeight}
						/>
						<RangeSlider
							label="Width"
							limit={WIDTH_LIMIT}
							bind:lower={filters.minWidth}
							bind:upper={filters.maxWidth}
						/>
						<fieldset class="traits">
							<legend>Plant traits</legend>
							<div class="checkboxes">
								{#each booleanFilters as { key, label } (key)}
									<label><input type="checkbox" bind:checked={filters[key]} />{label}</label>
								{/each}
							</div>
							<p>Checked traits are required. Unchecked traits are unrestricted.</p>
						</fieldset>
					</div>
					<div class="filter-footer">
						<button type="button" class="clear-button" onclick={() => (filters = createFilters())}
							>Clear filters</button
						>
						<button type="submit" class="primary" disabled={loading}>Apply filters</button>
					</div>
				</section>
			{/if}
		</form>
		<p id="search-status" role="status">{message}</p>
		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}
	</section>
	<div class="map-area">
		<div class="map-container">
			<Map height="100%" colorAssignment={assignment} onCountyClick={onClick} />
		</div>
		{#if selectedCounty}
			{#key selectedCounty.geoid}
				{#key completedSearch}
					<CountySidebar
						county={selectedCounty}
						query={completedSearch}
						matchCount={countyCounts[Number(selectedCounty.geoid)] ?? 0}
						searching={loading}
						searchError={error}
						{getPlantDetails}
						onclose={closeCounty}
					/>
				{/key}
			{/key}
		{/if}
	</div>
</main>

<style>
	main {
		display: flex;
		height: 100dvh;
		flex-direction: column;
		background: #eef3f1;
		color: #254d46;
	}
	.map-area {
		position: relative;
		display: flex;
		flex: 1;
		min-height: 0;
	}
	.map-container {
		flex: 1;
		min-width: 0;
	}
	.search-panel {
		position: relative;
		z-index: 2;
		padding: 16px 24px;
		border-bottom: 1px solid #cbd9d3;
		background: #fff;
		box-shadow: 0 2px 8px #254d460a;
	}
	form > label {
		display: block;
		margin-bottom: 8px;
		font-weight: 600;
	}
	.search-controls {
		display: flex;
		align-items: flex-start;
		gap: 8px;
	}
	.search-help {
		color: #60776c;
		font-size: 0.75rem;
	}
	button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 8px;
		border: 1px solid #cbd9d3;
		border-radius: 6px;
		padding: 10px 16px;
		background: white;
		cursor: pointer;
		font-weight: 500;
	}
	.primary {
		background: #254d46;
		color: white;
		border-color: #254d46;
	}
	button:hover:not(:disabled) {
		box-shadow: inset 0 0 0 1px #809c90;
	}
	.filter-toggle.active,
	.option[aria-pressed='true'] {
		background: #e7f1e9;
		border-color: #326b50;
		color: #1c5038;
	}
	.filter-count {
		padding: 0 6px;
		border-radius: 10px;
		background: #326b50;
		color: white;
		font-size: 0.75rem;
	}
	.filter-menu {
		position: absolute;
		top: calc(100% + 8px);
		right: 24px;
		width: min(660px, calc(100% - 48px));
		max-height: calc(100dvh - 100% - 20px);
		overflow-y: auto;
		border: 1px solid #cbd9d3;
		border-radius: 12px;
		background: white;
		box-shadow: 0 12px 40px #254d4626;
	}
	.filter-header,
	.filter-footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 20px 24px;
	}
	.filter-header {
		border-bottom: 1px solid #e5ebe7;
	}
	h2 {
		font-size: 1.125rem;
		font-weight: 600;
	}
	.filter-header p,
	.zones p,
	.traits p {
		margin-top: 4px;
		color: #60776c;
		font-size: 0.8125rem;
	}
	.close-button {
		padding: 4px 12px;
		border: 0;
		font-size: 1.5rem;
	}
	.filter-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 24px;
		padding: 24px;
	}
	fieldset {
		min-width: 0;
	}
	.zones {
		grid-column: 1 / -1;
	}
	.zone-range {
		display: flex;
		gap: 16px;
	}
	.zone-range > div {
		display: flex;
		flex: 1;
		min-width: 0;
		flex-direction: column;
		gap: 6px;
		font-size: 0.8125rem;
	}
	.zone-range select {
		width: 100%;
		padding: 8px;
		border: 1px solid #809c90;
		border-radius: 6px;
		background: #f7faf8;
		color: inherit;
	}
	legend {
		margin-bottom: 10px;
		font-weight: 600;
	}
	.options {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.option {
		padding: 8px 10px;
		font-size: 0.8125rem;
		text-transform: capitalize;
	}
	.traits {
		grid-column: 1 / -1;
	}
	.checkboxes {
		display: flex;
		flex-wrap: wrap;
		gap: 12px 24px;
		margin-bottom: 12px;
	}
	.checkboxes label {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		min-height: 28px;
		cursor: pointer;
	}
	input[type='checkbox'] {
		width: 17px;
		height: 17px;
		accent-color: #326b50;
	}
	.filter-footer {
		position: sticky;
		bottom: 0;
		z-index: 4;
		border-top: 1px solid #e5ebe7;
		background: white;
	}
	.clear-button {
		border-color: transparent;
		text-decoration: underline;
	}
	button:disabled {
		cursor: default;
		opacity: 0.6;
	}
	input:focus-visible,
	select:focus-visible,
	button:focus-visible {
		outline: 2px solid #254d46;
		outline-offset: 2px;
	}
	p {
		margin-top: 10px;
		font-size: 0.875rem;
	}
	.error {
		color: #922f2f;
	}
	@media (max-width: 600px) {
		.search-panel {
			padding: 12px;
		}
		.search-controls {
			gap: 6px;
			flex-wrap: wrap;
		}
		.search-controls :global(.search-box) {
			flex-basis: 100%;
		}
		.search-controls button {
			padding: 10px;
			gap: 4px;
			font-size: 0.875rem;
		}
		.filter-toggle svg {
			display: none;
		}
		.filter-menu {
			right: 12px;
			width: calc(100% - 24px);
		}
		.filter-grid {
			grid-template-columns: 1fr;
			padding: 20px;
		}
		.filter-header,
		.filter-footer {
			padding: 16px 20px;
		}
	}
</style>
