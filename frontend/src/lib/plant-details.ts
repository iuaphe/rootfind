export const apiUrl = 'https://mbvkjcluckluyjwscqkx.supabase.co/functions/v1';
export const apikey = 'sb_publishable_oqsF0ARtDJ2Rw8Z5jNiCKw_L_NTANOF';

export interface PlantDetails {
	plant_id: number;
	genus: string;
	species: string;
	common_names: string[];
	life_cycle?: string | null;
	foliage?: string | null;
	sun?: string | null;
	water?: string | string[] | null;
	minheight?: number | null;
	maxheight?: number | null;
	minwidth?: number | null;
	maxwidth?: number | null;
	is_woody?: boolean | null;
	is_tree?: boolean | null;
	edible?: boolean | null;
	thorns?: boolean | null;
	image_link?: string | null;
	misc?: string[] | null;
}

/** Keep the cache and request budget local to this page's client instance. */
export function createPlantDetailsClient() {
	const cache = new Map<number, PlantDetails>();
	let nextRequestAt = 0;

	return async (plantId: number, signal: AbortSignal): Promise<PlantDetails> => {
		signal.throwIfAborted();
		const cached = cache.get(plantId);
		if (cached) return cached;

		// plant-details accepts one ID and allows 120 requests per minute.
		const requestAt = Math.max(Date.now(), nextRequestAt);
		nextRequestAt = requestAt + 550;
		const delay = requestAt - Date.now();
		if (delay > 0) {
			await new Promise<void>((resolve, reject) => {
				const abort = () => {
					clearTimeout(timer);
					reject(signal.reason);
				};
				const timer = setTimeout(() => {
					signal.removeEventListener('abort', abort);
					resolve();
				}, delay);
				signal.addEventListener('abort', abort, { once: true });
			});
		}
		signal.throwIfAborted();
		const response = await fetch(
			`${apiUrl}/plant-details?${new URLSearchParams({ plantId: String(plantId) })}`,
			{
				headers: { apikey },
				signal
			}
		);
		if (!response.ok) {
			throw new Error(
				response.status === 429
					? 'Plant details are temporarily rate limited. Please wait a minute, then retry.'
					: 'Unable to load all plant details. Please retry.'
			);
		}
		const plant = await response.json();
		if (
			!plant ||
			plant.plant_id !== plantId ||
			typeof plant.genus !== 'string' ||
			typeof plant.species !== 'string' ||
			!Array.isArray(plant.common_names) ||
			!plant.common_names.every((name: unknown) => typeof name === 'string') ||
			(plant.misc != null &&
				(!Array.isArray(plant.misc) ||
					!plant.misc.every((note: unknown) => typeof note === 'string')))
		) {
			throw new Error('Unexpected plant details response. Please retry.');
		}
		cache.set(plantId, plant);
		return plant;
	};
}
