// Starts an isolated backend for the browser tests: a fresh SQLite file,
// the deterministic "scripted" model provider, and the scheduler off.
// Nothing here touches the developer's real database or model.

import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const backend = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'backend')
const windows = process.platform === 'win32'
const python =
  process.env.E2E_PYTHON ?? (windows ? join(backend, '.venv', 'Scripts', 'python.exe') : 'python')
const port = process.env.E2E_API_PORT ?? '8100'
const db = join(mkdtempSync(join(tmpdir(), 'fullhouse-e2e-')), 'e2e.db').replace(/\\/g, '/')

const env = {
  ...process.env,
  DATABASE_URL: `sqlite:///${db}`,
  LLM_PROVIDER: 'scripted',
  SCHEDULER_ENABLED: 'false',
  FRONTEND_ORIGIN: process.env.E2E_WEB_ORIGIN ?? 'http://127.0.0.1:5174',
  PYTHONUNBUFFERED: '1',
}

for (const args of [['-m', 'alembic', 'upgrade', 'head'], ['scripts/seed_data.py'], ['scripts/seed_e2e.py']]) {
  const result = spawnSync(python, args, { cwd: backend, env, stdio: 'inherit' })
  if (result.status !== 0) {
    console.error(`e2e backend setup failed: ${python} ${args.join(' ')}`)
    process.exit(result.status ?? 1)
  }
}

const server = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', port], {
  cwd: backend,
  env,
  stdio: 'inherit',
})
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.kill(signal))
server.on('exit', (code) => process.exit(code ?? 0))
