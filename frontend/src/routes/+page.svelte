<script lang="ts">
	import { Map } from '#lib';

	const colors = ['#aecbbb', '#72b3a2', '#448c7c', '#286c61', '#c9b97f'];
</script>

<Map colorAssignment={(county) => colors[Number(county.geoid) % colors.length]} />
