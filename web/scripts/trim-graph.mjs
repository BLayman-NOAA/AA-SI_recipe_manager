// Trim a graph JSON read on stdin for use as a test fixture: every description,
// implementation, op_url and hash becomes null; compact JSON goes to stdout.
// Usage: aa-recipe graph <recipe>.yaml --json | node scripts/trim-graph.mjs > fixture.json
const TRIMMED = new Set(['description', 'implementation', 'op_url', 'hash']);

function trim(value) {
  if (Array.isArray(value)) return value.map(trim);
  if (value === null || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, TRIMMED.has(key) ? null : trim(item)]));
}

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
process.stdout.write(JSON.stringify(trim(JSON.parse(Buffer.concat(chunks).toString('utf8')))));
