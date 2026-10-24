import type { CountyFeature, CountyProperties } from '../types/counties';

/** CSS colors keyed by five-character county GEOID, or a function of county attributes. */
export type CountyColorAssignment =
	Readonly<Record<string, string | undefined>> | ((county: CountyProperties) => string | undefined);

export const MAP_BOUNDS = {
	'united-states': [-190, 18, -66, 72],
	contiguous: [-125, 24, -66, 50],
	alaska: [-190, 51, -129, 72],
	hawaii: [-161, 18, -154, 23],
	'puerto-rico': [-67.4, 17.8, -65.2, 18.6],
	'virgin-islands': [-65.1, 17.6, -64.5, 18.5],
	'american-samoa': [-171.2, -14.7, -168, -10.8],
	'guam-mariana': [144.4, 13.1, 146.2, 20.7]
} satisfies Record<string, [number, number, number, number]>;

export type MapView = keyof typeof MAP_BOUNDS;

export interface MapProps {
	colorAssignment?: CountyColorAssignment;
	/** Color for counties without an assigned color. */
	defaultColor?: string;
	/** Receives the complete original feature, including its boundary and string GEOID. */
	onCountyClick?: (county: CountyFeature, event: MouseEvent) => void;
	initialView?: MapView;
	height?: string;
	class?: string;
	'aria-label'?: string;
}
