<script lang="ts">
	import { Map, type CountyColorAssignment, type CountyFeature, type CountyProperties } from '#lib';
	import { onMount } from 'svelte';

	let plantIds: number[];
	let g: number[][];
	let assignment: CountyColorAssignment = $state(() => `#bbb`);

	onMount(async () => {
		plantIds = await fetch(
			'https://mbvkjcluckluyjwscqkx.supabase.co/functions/v1/search-plants?search=carya',
			{
				headers: {
					apikey: 'sb_publishable_oqsF0ARtDJ2Rw8Z5jNiCKw_L_NTANOF'
				}
			}
		).then((f) => f.json());

		g = await fetch('https://mbvkjcluckluyjwscqkx.supabase.co/functions/v1/plant-counties', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				apikey: 'sb_publishable_oqsF0ARtDJ2Rw8Z5jNiCKw_L_NTANOF'
			},
			body: JSON.stringify({
				plantIds
			})
		}).then((f) => f.json());

		console.log(plantIds);
		console.log(g);

		assignment = (c: CountyProperties) => {
			let num = g.filter((x) => x.includes(parseInt(c.geoid))).length;
			let amount = Math.max(255 - num * 30, 0);
			return `rgb(${amount}, 255, ${amount})`;
		};
	});

	const onClick = (c: CountyFeature) => {
		alert(
			`${c.properties.name}: ${plantIds.filter((x, i) => g[i].includes(parseInt(c.properties.geoid)))}`
		);
	};
</script>

<Map colorAssignment={assignment} onCountyClick={onClick} />
