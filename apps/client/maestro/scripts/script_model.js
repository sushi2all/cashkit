// Queue the provider's answer for the change turn.
//
// Only the provider is scripted (D-MLP-34). The guard, the dry-run, the
// proposal store and every endpoint the app touches are real.
http.post(HARNESS + '/__control/script', {
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    replace: true,
    responses: [
      {
        kind: 'answer',
        reply: 'I will add a gym membership.',
        intents: [
          { op: 'add_item', id: 'gym', direction: 'out', amount: '-49.90',
            recurrence: '1m', start: '2026-04-01' },
        ],
      },
    ],
  }),
});
