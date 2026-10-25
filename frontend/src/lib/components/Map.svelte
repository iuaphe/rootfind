<script lang="ts">
	import { onMount } from 'svelte';
	import type { ExpressionSpecification, Map as MapLibreMap } from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import countiesUrl from '../data/counties.geojson?url';
	import type { CountyFeatureCollection } from '../types/counties';
	import { MAP_BOUNDS, type MapProps } from './map';
	import { COUNTY_PROJECTION, initializeCountyProjection } from './map-projection';

	let {
		colorAssignment = {},
		defaultColor = '#65a99b',
		onCountyClick,
		initialView = 'united-states',
		height = '100vh',
		class: className = '',
		'aria-label': ariaLabel = 'Interactive United States county map'
	}: MapProps = $props();

	const SOURCE = 'counties';
	const FILL = 'county-fill';
	let container: HTMLDivElement;
	let map = $state.raw<MapLibreMap>();
	let counties = $state.raw<CountyFeatureCollection>();
	let ready = $state(false);
	let error = $state('');
	let hoveredId: string | undefined;

	function clearHover() {
		if (hoveredId !== undefined && map?.getSource(SOURCE)) {
			map.removeFeatureState({ source: SOURCE, id: hoveredId }, 'hover');
		}
		hoveredId = undefined;
		if (map) map.getCanvas().style.cursor = '';
	}

	// Updating colors only changes GPU paint; the boundary source stays loaded.
	$effect(() => {
		if (!ready || !map || !counties) return;
		const colors: Record<string, string> = {};
		for (const { properties } of counties.features) {
			const color =
				typeof colorAssignment === 'function'
					? colorAssignment(properties)
					: colorAssignment[properties.geoid];
			if (color !== undefined) colors[properties.geoid] = color;
		}
		const expression: ExpressionSpecification = [
			'to-color',
			['get', ['get', 'geoid'], ['literal', colors]],
			defaultColor
		];
		map.setPaintProperty(FILL, 'fill-color', expression);
	});

	onMount(() => {
		const abort = new AbortController();
		let disposed = false;
		let instance: MapLibreMap | undefined;
		let observer: ResizeObserver | undefined;

		async function initialize() {
			try {
				// Import the renderer only in the browser so this component supports SSR.
				const [maplibre, response] = await Promise.all([
					import('maplibre-gl'),
					fetch(countiesUrl, { signal: abort.signal })
				]);
				if (!response.ok) throw new Error(`County data request failed (${response.status}).`);
				const data: CountyFeatureCollection = await response.json();
				if (disposed) return;
				counties = data;
				initializeCountyProjection(maplibre);
				const byId = new globalThis.Map(data.features.map((county) => [county.id, county]));
				instance = new maplibre.Map({
					container,
					bounds: MAP_BOUNDS[initialView],
					fitBoundsOptions: { padding: 36 },
					minZoom: 0,
					maxZoom: 12,
					clickTolerance: 4,
					dragPan: true,
					scrollZoom: true,
					dragRotate: false,
					touchPitch: false,
					pitchWithRotate: false,
					attributionControl: false,
					style: {
						version: 8,
						projection: { type: COUNTY_PROJECTION },
						sources: {},
						layers: [
							{ id: 'background', type: 'background', paint: { 'background-color': '#eef3f1' } }
						]
					}
				});
				const current = instance;
				map = current;
				current.touchZoomRotate.disableRotation();
				current.keyboard.disableRotation();
				current.getCanvas().setAttribute('aria-label', ariaLabel);
				current.on('error', (event) => {
					error = `Unable to render the county map: ${event.error.message}`;
				});
				current.on('webglcontextlost', () => {
					error = 'The graphics connection was interrupted. Waiting for it to recover…';
				});
				current.on('webglcontextrestored', () => {
					error = '';
				});
				current.on('load', () => {
					current.addSource(SOURCE, {
						type: 'geojson',
						data,
						// Preserve FIPS IDs such as "01001" through GeoJSON tiling.
						promoteId: 'geoid'
					});
					current.addLayer({
						id: FILL,
						type: 'fill',
						source: SOURCE,
						paint: { 'fill-color': defaultColor }
					});
					current.addLayer({
						id: 'county-hover',
						type: 'fill',
						source: SOURCE,
						paint: {
							'fill-color': '#ffffff',
							'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.18, 0]
						}
					});
					current.addLayer({
						id: 'county-borders',
						type: 'line',
						source: SOURCE,
						paint: {
							'line-color': '#254d46',
							'line-opacity': 0.38,
							'line-width': ['interpolate', ['linear'], ['zoom'], 2, 0.25, 7, 0.8]
						}
					});
					current.on('mousemove', FILL, (event) => {
						if (current.isMoving()) return;
						const id = event.features?.[0]?.properties.geoid;
						if (typeof id !== 'string' || id === hoveredId) return;
						clearHover();
						hoveredId = id;
						current.setFeatureState({ source: SOURCE, id }, { hover: true });
						current.getCanvas().style.cursor = 'pointer';
					});
					current.on('mouseleave', FILL, clearHover);
					current.on('movestart', clearHover);
					current.on('dragstart', () => {
						current.getCanvas().style.cursor = 'grabbing';
					});
					current.on('dragend', () => {
						current.getCanvas().style.cursor = '';
					});
					current.on('click', FILL, (event) => {
						const county = byId.get(event.features?.[0]?.properties.geoid);
						if (county) onCountyClick?.(county, event.originalEvent);
					});
					ready = true;
				});
				observer = new ResizeObserver(() => current.resize());
				observer.observe(container);
			} catch (cause) {
				if (!disposed)
					error = cause instanceof Error ? cause.message : 'Unable to load the county map.';
			}
		}

		void initialize();
		return () => {
			disposed = true;
			abort.abort();
			observer?.disconnect();
			instance?.remove();
		};
	});
</script>

<section
	class="county-map {className}"
	style:height
	aria-label={ariaLabel}
	aria-busy={!ready && !error}
>
	<div class="canvas" bind:this={container}></div>
	{#if error}
		<div class="message error" role="alert">{error}</div>
	{:else if !ready}
		<div class="message" role="status">Loading county boundaries…</div>
	{/if}
</section>

<style>
	.county-map {
		position: relative;
		width: 100%;
		min-height: 320px;
		overflow: hidden;
	}
	.canvas {
		position: absolute;
		inset: 0;
	}
	.message {
		position: absolute;
		inset: 0;
		display: grid;
		place-content: center;
		padding: 32px;
		background: #eef3f1ed;
		text-align: center;
	}
	.error {
		color: #922f2f;
	}
</style>
