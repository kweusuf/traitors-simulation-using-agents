import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { z } from 'zod';

const here = path.dirname(fileURLToPath(import.meta.url));
const dataDir = path.resolve(here, '../src/data');

const patterns = [
  ['ipv4', /\b(?:\d{1,3}\.){3}\d{1,3}\b/g],
  ['local-path', /(?:\/Users\/|\/Volumes\/|\/home\/|\/private\/|[A-Za-z]:\\\\|\/tmp\/)/g],
  ['digest', /\b(?:sha256:)?[0-9a-f]{64}\b/gi],
  ['local-host', /\b[a-z0-9][a-z0-9.-]*\.(?:local|lan|internal)\b/gi],
  ['api-key', /\b(?:sk-|ghp_|glpat-|xox[baprs]-)[A-Za-z0-9_-]{12,}\b/g],
  ['base-url', /base_url/gi],
  ['api-key-name', /api_key/gi],
  [
    'runtime-metric',
    /\b(?:latency|duration|in[_-]?flight|throughput|tok\/s)[\w\s-]*?[:=]/gi,
  ],
];

const failures = [];
function fail(message) {
  failures.push(message);
}

const RunRow = z.object({
  id: z.string(),
  slug: z.string().nullable().optional(),
  flagship: z.boolean(),
  players: z.number().int().positive(),
  rounds: z.number().int().positive(),
  messages: z.number().int().nonnegative(),
  winner: z.string().nullable(),
  calls: z.number().int().nonnegative(),
  totalTokens: z.number().int().nonnegative(),
  eliminationCount: z.number().int().optional(),
});

const Manifest = z.object({
  version: z.number(),
  generatedFrom: z.array(z.string()),
  totals: z.object({
    runs: z.number().int(),
    playerGames: z.number().int(),
    messages: z.number().int(),
    calls: z.number().int(),
    tokens: z.number().int(),
  }),
  runs: z.array(RunRow),
});

const Player = z.object({
  id: z.string(),
  name: z.string(),
  role: z.enum(['faithful', 'traitor']),
  roleByRound: z.record(z.string(), z.enum(['faithful', 'traitor'])),
  aliveByRound: z.record(z.string(), z.boolean()),
  stats: z.record(z.string(), z.unknown()),
});

const Summary = z.object({
  id: z.string(),
  flagship: z.boolean(),
  players: z.array(Player),
  rounds: z.number().int().positive(),
  roundDigests: z.array(z.object({ r: z.number().int() })),
  outcome: z.object({ winner: z.string().nullable() }),
  counts: z.object({ messages: z.number().int().nonnegative() }),
  config: z.object({ name: z.string(), provider: z.string(), model: z.string() }),
});

const Messages = z.object({
  messages: z.array(
    z.object({
      r: z.number().int().nonnegative(),
      phase: z.string(),
      channel: z.enum(['public', 'private', 'traitor', 'host']),
      from: z.string(),
      to: z.array(z.string()),
      text: z.string(),
    }),
  ),
});

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
}

const manifestPath = path.join(dataDir, 'manifest.json');
const manifest = Manifest.parse(readJson(manifestPath));
const ids = new Set();

for (const row of manifest.runs) {
  if (ids.has(row.id)) fail(`duplicate run id: ${row.id}`);
  ids.add(row.id);

  const summaryPath = path.join(dataDir, 'runs', row.id, 'summary.json');
  if (!fs.existsSync(summaryPath)) {
    fail(`missing summary for ${row.id}`);
    continue;
  }
  const summary = Summary.parse(readJson(summaryPath));
  if (summary.id !== row.id) fail(`summary id mismatch for ${row.id}`);
  if (summary.players.length !== row.players) fail(`player count mismatch for ${row.id}`);
  if (summary.roundDigests.length !== summary.rounds) {
    fail(`round digest count mismatch for ${row.id}`);
  }
  const roster = new Set(summary.players.map((player) => player.id));
  if (summary.flagship) {
    const messagesPath = path.join(dataDir, 'runs', row.id, 'messages.json');
    if (!fs.existsSync(messagesPath)) {
      fail(`flagship ${row.id} has no messages.json`);
    } else {
      const { messages } = Messages.parse(readJson(messagesPath));
      if (messages.length === 0) fail(`flagship ${row.id} has no messages`);
      for (const message of messages) {
        if (message.from !== 'host' && !roster.has(message.from)) {
          fail(`unknown sender ${message.from} in ${row.id}`);
          break;
        }
        for (const recipient of message.to) {
          if (!roster.has(recipient)) {
            fail(`unknown recipient ${recipient} in ${row.id}`);
            break;
          }
        }
      }
    }
  }
}

for (const file of walk(dataDir)) {
  if (!file.endsWith('.json')) continue;
  const text = fs.readFileSync(file, 'utf8');
  for (const [label, pattern] of patterns) {
    pattern.lastIndex = 0;
    const match = pattern.exec(text);
    if (match) {
      fail(`${path.relative(dataDir, file)} contains ${label}: ${match[0].slice(0, 60)}`);
    }
  }
}

if (failures.length) {
  console.error(`Data validation failed with ${failures.length} issue(s):`);
  for (const failure of failures.slice(0, 50)) console.error(`  - ${failure}`);
  process.exit(1);
}

console.log(
  `Data OK: ${manifest.runs.length} runs, ${manifest.totals.playerGames} player-games, ` +
    `${manifest.totals.messages} messages, ${manifest.totals.calls} calls.`,
);
