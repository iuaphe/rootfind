# County map

`Map.svelte` renders the local 2018 Census county boundaries with MapLibre GL JS
(WebGL). No API key, remote basemap, or tile service is needed. The GeoJSON is
loaded once in the browser, separately from the JavaScript bundle.

The map uses an Albers equal-area projection (WGS84, standard parallels 29.5°
and 45.5°, central meridian 96°W). Alaska and the other states share the same
area scale. The source GeoJSON and callback features remain in WGS84 longitude/
latitude; `map-projection.ts` configures the display projection and installs a
local proj4 converter in each MapLibre tiling worker at startup.
Custom CRS support requires MapLibre GL JS 6.15 or later.

```svelte
<script lang="ts">
	import { Map, type CountyFeature } from '#lib';

	let colors = $state({ '06037': '#d58a65', '36061': '#649cbb' });

	function selectCounty(county: CountyFeature, event: MouseEvent) {
		console.log(county.id, county.properties.name, event);
	}
</script>

<Map colorAssignment={colors} onCountyClick={selectCounty} height="600px" />
```

`colorAssignment` accepts either an object keyed by five-character county GEOID or
a function `(county: CountyProperties) => string | undefined`. Colors use MapLibre
CSS color syntax (for example hex, RGB, HSL, or named colors). Missing entries use
`defaultColor`, which defaults to `#65a99b`. Updates to reactive assignments and
state read by a color function repaint the map without reloading boundaries.
Each county fades from its current color to the new color over 5 seconds.
An update during a fade continues from the displayed color. The initial colors
appear immediately. This color-only fade also runs with reduced motion enabled.
Keep identifiers as strings to preserve leading zeros.

`onCountyClick` receives the original complete `CountyFeature` and the mouse event.
Dragging is handled separately from clicking. A translucent white overlay slightly
lightens the hovered county, including all of its islands and disconnected parts.

`initialView` defaults to `united-states`, fitting all 50 states. It also accepts
`contiguous`, `alaska`, `hawaii`, `puerto-rico`, `virgin-islands`, `american-samoa`,
and `guam-mariana`. All 3,233 features remain available regardless of the initial
view. The projection keeps Aleutian islands continuous across the date line
without repeating the world.

Left-drag pans; wheel/trackpad scroll zooms around the pointer. Touch panning and
pinching and keyboard arrows and +/− are supported. Rotation is disabled to keep
north up. The map has no controls, tooltips, or instruction overlays. `height`,
`class`, and `aria-label` can be customized. The component handles container resizes
and releases its WebGL context, workers, event listeners, resize observer, and
pending fetch when unmounted.

The data is copied from `data-processing/output/counties.geojson` to
`src/lib/data/counties.geojson`; types are copied from `data-processing/types.ts`
to `src/lib/types/counties.ts`. Refresh both copies if the extraction schema changes.
The boundaries reflect 2018 geography and include county equivalents and territories.
