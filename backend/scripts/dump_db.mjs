import { spawn } from 'node:child_process';
import { createReadStream, createWriteStream } from 'node:fs';
import { appendFile, mkdir, mkdtemp, rename, rm, stat, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const args = process.argv.slice(2);
if (args.some((arg) => arg !== '--local') || args.length > 1) {
  console.error('Usage: npm run db:dump -- [--local]');
  process.exit(1);
}

// The CLI runs pg_dump in a disposable Supabase Postgres container. It only
// reads the source database; no local server or migration/reset is needed.
const target = args.includes('--local')
  ? ['--local']
  : process.env.SUPABASE_DB_URL
    ? ['--db-url', process.env.SUPABASE_DB_URL]
    : ['--linked'];

function run(command, commandArgs) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, commandArgs, { cwd: root, stdio: 'inherit' });
    child.once('error', reject);
    child.once('exit', (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} failed (${signal ?? code}).`));
    });
  });
}

process.umask(0o077);
const backups = join(root, 'backups');
let staging;
try {
  await run('docker', ['info', '--format', '{{.ServerVersion}}']);
  await mkdir(backups, { recursive: true });
  staging = await mkdtemp(join(backups, '.dump-'));

  for (const [name, flags] of [
    ['roles.sql', ['--role-only']],
    ['schema.sql', []],
    ['data.sql', ['--data-only', '--use-copy',
      '--exclude', 'storage.buckets_vectors', '--exclude', 'storage.vector_indexes']],
  ]) {
    console.log(`Exporting ${name}…`);
    const file = join(staging, name);
    await run(process.platform === 'win32' ? 'npx.cmd' : 'npx', [
      '--no-install', 'supabase', 'db', 'dump', ...target, ...flags, '--file', file,
    ]);
    if ((await stat(file)).size === 0) throw new Error(`Empty export: ${name}`);
  }

  const combined = join(staging, 'rootfind.sql');
  await writeFile(combined, '-- Supabase logical backup: roles, schema, then data.\n'
    + '-- Restore into a fresh Supabase database using psql -v ON_ERROR_STOP=1 --single-transaction -f rootfind.sql.\n');
  for (const name of ['roles.sql', 'schema.sql', 'data.sql']) {
    await appendFile(combined, `\n-- ${name}\n`);
    if (name === 'data.sql') {
      await appendFile(combined, 'SET session_replication_role = replica;\n');
    }
    await pipeline(createReadStream(join(staging, name)), createWriteStream(combined, { flags: 'a' }));
    await appendFile(combined, '\n');
  }
  await appendFile(combined, 'SET session_replication_role = origin;\n');
  const timestamp = new Date().toISOString().replaceAll(':', '-').replaceAll('.', '-');
  const destination = join(backups, `rootfind-${timestamp}-${staging.slice(-6)}`);
  await rename(staging, destination);
  staging = undefined;
  console.log(`\nSQL backup: ${join(destination, 'rootfind.sql')}`);
} catch (error) {
  if (staging) await rm(staging, { recursive: true, force: true });
  console.error(`\nDatabase export failed: ${error.message}`);
  process.exitCode = 1;
}
