var health = http.get(HARNESS + '/__control/health');
if (!health.ok) {
  throw new Error(
    'the E2E harness is not running at ' + HARNESS + '. Start it first:\n' +
    '  uv run python apps/client/e2e/harness/server.py --port 8099'
  );
}
