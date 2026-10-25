import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
	DATABASE_URL: {
		description: 'Optional direct database connection for server-side Drizzle usage.',
		schema: (value: string | undefined) => value
	}
});
