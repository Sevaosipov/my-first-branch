import { appendFile, readFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_LOG_PATH = fileURLToPath(new URL('../data/alerts.log', import.meta.url));

export async function logAlert(entry, logPath = DEFAULT_LOG_PATH) {
  await mkdir(dirname(logPath), { recursive: true });
  await appendFile(logPath, `${JSON.stringify(entry)}\n`, 'utf8');
}

export async function readAlerts(logPath = DEFAULT_LOG_PATH, limit = 50) {
  try {
    const content = await readFile(logPath, 'utf8');
    const lines = content.trim().split('\n').filter(Boolean);
    return lines
      .slice(-limit)
      .map((line) => JSON.parse(line))
      .reverse();
  } catch (err) {
    if (err.code === 'ENOENT') return [];
    throw err;
  }
}
