// Create the book through the API.
//
// The gate permits this: "book (API-seeded via POST /books is acceptable here;
// the UI path is session S5's gate)". The horizon brackets the harness's frozen
// clock, so `as_of` falls inside it.
//
// The two items still go through a proposal and an accept. Nothing reaches a
// book without an accepted proposal, not even a fixture (ADR-0029).
var verify = http.post(HARNESS + '/api/auth/verify', {
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ token: output.link.token, platform: 'mobile' }),
});
var session = json(verify.body);
var auth = { 'Content-Type': 'application/json', Authorization: 'Bearer ' + session.token };

http.post(HARNESS + '/api/books', {
  headers: auth,
  body: JSON.stringify({
    horizon_start: '2026-01-01',
    horizon_end: '2027-01-01',
    opening_balance: '2500.00',
  }),
});

var edits = http.post(HARNESS + '/api/book/edits', {
  headers: auth,
  body: JSON.stringify({
    origin: 'onboarding',
    ops: [
      { op: 'add_item', id: 'salary', name: 'Salary', direction: 'in',
        amount: '2617.33', recurrence: '1m', start: '2026-01-01' },
      { op: 'add_item', id: 'rent', name: 'Rent', direction: 'out',
        amount: '-912.50', recurrence: '1m', start: '2026-01-01' },
    ],
  }),
});
var proposal = json(edits.body).proposal;
http.post(HARNESS + '/api/proposals/' + proposal.id, {
  headers: auth,
  body: JSON.stringify({ action: 'accept' }),
});
http.post(HARNESS + '/api/book/save', {
  headers: auth,
  body: JSON.stringify({ message: 'seed' }),
});
