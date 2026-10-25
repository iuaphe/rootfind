import { Color } from '@maplibre/maplibre-gl-style-spec';
import type { ExpressionSpecification, Map as MapLibreMap } from 'maplibre-gl';

const DURATION = 200;
const PROGRESS = 'county-color-progress';
type Colors = Record<string, Color>;

function expression(colors: Colors): ExpressionSpecification {
	return [
		'to-color',
		[
			'get',
			['get', 'geoid'],
			[
				'literal',
				Object.fromEntries(
					Object.entries(colors).map(([id, color]) => {
						const [r, g, b, a] = color.rgb;
						return [id, `rgba(${r * 255},${g * 255},${b * 255},${a})`];
					})
				)
			]
		]
	];
}

/** Animate one shared progress value; county color tables change only on assignment. */
export function createCountyColorTransition(map: MapLibreMap, layer: string) {
	let from: Colors = {};
	let target: Colors = {};
	let progress = 1;
	let frame = 0;
	let initialized = false;

	return {
		set(colors: Record<string, string>, defaultColor: string) {
			cancelAnimationFrame(frame);
			const fallback = Color.parse(defaultColor) ?? Color.transparent;
			const parsed = new Map<string, Color>();
			const next = Object.fromEntries(
				Object.entries(colors).map(([id, value]) => {
					if (!parsed.has(value)) parsed.set(value, Color.parse(value) ?? fallback);
					return [id, parsed.get(value)!];
				})
			);
			// Sample the last displayed blend so a new assignment never jumps back.
			from = Object.fromEntries(
				Object.entries(next).map(([id, color]) => [
					id,
					initialized
						? Color.interpolate(from[id] ?? target[id] ?? color, target[id] ?? color, progress)
						: color
				])
			);
			target = next;
			if (!initialized) {
				initialized = true;
				progress = 1;
				map.setPaintProperty(layer, 'fill-color', expression(target));
				return;
			}
			progress = 0;
			map.setGlobalStateProperty(PROGRESS, progress);
			map.setPaintProperty(layer, 'fill-color', [
				'interpolate',
				['linear'],
				['global-state', PROGRESS],
				0,
				expression(from),
				1,
				expression(target)
			]);
			const start = performance.now();
			function animate(now: number) {
				const t = Math.min((now - start) / DURATION, 1);
				progress = t * t * (3 - 2 * t);
				map.setGlobalStateProperty(PROGRESS, progress);
				if (t < 1) frame = requestAnimationFrame(animate);
				else map.setPaintProperty(layer, 'fill-color', expression(target));
			}
			frame = requestAnimationFrame(animate);
		},
		destroy() {
			cancelAnimationFrame(frame);
		}
	};
}
