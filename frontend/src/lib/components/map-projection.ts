import proj4 from 'proj4';
import type * as MapLibre from 'maplibre-gl';
import workerUrl from './map-projection-worker.ts?worker&url';

export const COUNTY_PROJECTION = 'rootfind:albers-equal-area';

// One area scale for every county, including Alaska (no inset or resizing).
// The central meridian keeps the Aleutians continuous across the date line.
export const ALBERS_DEFINITION =
	'+proj=aea +lat_1=29.5 +lat_2=45.5 +lat_0=37.5 +lon_0=-96 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs';

let registered = false;

/** Register once per browser session, also when multiple maps mount together. */
export function initializeCountyProjection(maplibre: typeof MapLibre): void {
	maplibre.setWorkerUrl(workerUrl);
	if (!registered) {
		maplibre.addProjection({
			name: COUNTY_PROJECTION,
			definition: ALBERS_DEFINITION,
			projection: proj4('EPSG:4326', ALBERS_DEFINITION),
			// A single square grid in meters covering all states and territories.
			tileMatrixSet: {
				origin: [-20_000_000, 20_000_000],
				extentAtZoom0: 40_000_000
			}
		});
		registered = true;
	}
}
