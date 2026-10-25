// @ts-expect-error MapLibre publishes this worker entry without type declarations.
import MapLibreWorker from 'maplibre-gl/dist/maplibre-gl-worker.mjs';
import proj4 from 'proj4';
import type { WorkerGlobalScopeInterface } from 'maplibre-gl';

const scope = self as unknown as WorkerGlobalScopeInterface;
// Retain the worker entry in production even though MapLibre marks its package
// as side-effect free. Its module normally initializes scope.worker itself.
scope.worker ??= new MapLibreWorker(self);
// Install the converter as each worker starts, including after map remounts.
scope.setProjectionHandler((definition) => proj4('EPSG:4326', definition));
