// Read the magic link out of the harness's test mailer.
//
// The service never returns a link token in an HTTP response, in any mode — a
// debug flag that did would defeat the whole single-use flow. The harness holds
// the mail that would have been sent.
var response = http.get(HARNESS + '/__control/link?email=' + encodeURIComponent(EMAIL));
if (!response.ok) {
  throw new Error('no magic link for ' + EMAIL + ': ' + response.body);
}
output.link = json(response.body);
