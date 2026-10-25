export const enumFilters = [
	{ key: 'lifeCycle', label: 'Life cycle', options: ['perennial', 'biennial', 'annual'] },
	{ key: 'foliage', label: 'Foliage', options: ['deciduous', 'evergreen'] },
	{ key: 'sun', label: 'Sun', options: ['full sun', 'part shade', 'full shade'] },
	{ key: 'water', label: 'Water', options: ['high', 'med', 'low'] },
	{
		key: 'bloomMonths',
		label: 'Bloom months',
		options: [
			'January',
			'February',
			'March',
			'April',
			'May',
			'June',
			'July',
			'August',
			'September',
			'October',
			'November',
			'December'
		]
	},
	{ key: 'bloomSeasons', label: 'Bloom seasons', options: ['Spring', 'Summer', 'Fall', 'Winter'] },
	{
		key: 'drainage',
		label: 'Drainage',
		options: ['good drainage', 'frequent standing water', 'occasionally wet', 'occasionally dry']
	}
] as const;

export const booleanFilters = [
	{ key: 'isWoody', label: 'Woody' },
	{ key: 'isTree', label: 'Tree' },
	{ key: 'edible', label: 'Edible' },
	{ key: 'thorns', label: 'Has thorns' }
] as const;

export const HEIGHT_LIMIT = 300;
export const WIDTH_LIMIT = 120;

export type EnumFilterKey = (typeof enumFilters)[number]['key'];
type BooleanFilterKey = (typeof booleanFilters)[number]['key'];
export type PlantFilters = Record<EnumFilterKey, string[]> &
	Record<BooleanFilterKey, boolean> & {
		minZone: number | undefined;
		maxZone: number | undefined;
		minHeight: number;
		maxHeight: number;
		minWidth: number;
		maxWidth: number;
	};

export function createFilters(): PlantFilters {
	return {
		lifeCycle: [],
		foliage: [],
		sun: [],
		water: [],
		bloomMonths: [],
		bloomSeasons: [],
		drainage: [],
		minZone: undefined,
		maxZone: undefined,
		isWoody: false,
		isTree: false,
		edible: false,
		thorns: false,
		minHeight: 0,
		maxHeight: HEIGHT_LIMIT,
		minWidth: 0,
		maxWidth: WIDTH_LIMIT
	};
}

export function filterParams(filters: PlantFilters): URLSearchParams {
	const params = new URLSearchParams();
	for (const { key } of enumFilters) {
		if (filters[key].length) params.set(key, filters[key].join(','));
	}
	for (const { key } of booleanFilters) {
		if (filters[key]) params.set(key, 'true');
	}
	if (filters.minZone !== undefined) params.set('minZone', String(filters.minZone));
	if (filters.maxZone !== undefined) params.set('maxZone', String(filters.maxZone));
	if (filters.minHeight > 0) params.set('minHeight', String(filters.minHeight));
	if (filters.maxHeight < HEIGHT_LIMIT) params.set('maxHeight', String(filters.maxHeight));
	if (filters.minWidth > 0) params.set('minWidth', String(filters.minWidth));
	if (filters.maxWidth < WIDTH_LIMIT) params.set('maxWidth', String(filters.maxWidth));
	return params;
}

export function countFilters(filters: PlantFilters): number {
	return (
		enumFilters.filter(({ key }) => filters[key].length > 0).length +
		booleanFilters.filter(({ key }) => filters[key]).length +
		Number(filters.minZone !== undefined || filters.maxZone !== undefined) +
		Number(filters.minHeight > 0 || filters.maxHeight < HEIGHT_LIMIT) +
		Number(filters.minWidth > 0 || filters.maxWidth < WIDTH_LIMIT)
	);
}
