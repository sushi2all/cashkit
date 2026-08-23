# Deploying CashKit — staging, then production

SPEC §12. One Hetzner Cloud VM in the EU per environment, Docker Compose,
Caddy terminating TLS in front of the service, Postgres in a container with a
volume, a backup sidecar writing to S3-compatible object storage in the EU, and
a metrics agent remote-writing to Grafana Cloud EU.

**Status, honestly:** everything in this document is written and every part of
it that can run without a cloud account **has been run** — the stack below was
brought up on a laptop with the same files, migrated, authenticated, imported a
real workbook through a real Caddy, and measured. **No Hetzner VM was created.**
See §7 of `km/notes/handoff-mlp-s6.md` for what that means and who owns it.

---

## 0. What you need before you start

| Thing | Why | Where it goes |
|---|---|---|
| A Hetzner Cloud project, EU location (`nbg1`, `fsn1` or `hel1`) | SPEC §9 EU-region hosting | — |
| A DNS name pointing at the VM | Caddy gets its certificate over HTTP-01, which needs the name to resolve first | `SITE_ADDRESS` |
| An S3-compatible bucket in the EU | Backups (SPEC §2.2) | `S3_*` |
| An `age` keypair | Encryption at rest for backups (SPEC §9) | public half → `BACKUP_AGE_RECIPIENT`; **keep the identity off the VM** |
| An OpenRouter key **per environment** | Staging gets its own with its own spend cap | `OPENROUTER_API_KEY` |
| A mail provider key | Magic links. Without it `ConsoleMailer` prints links to the log — acceptable on staging, never for an external user | `MAIL_PROVIDER_API_KEY` |
| A Grafana Cloud stack, EU region | SPEC §11: the alarms live off the VM | `GRAFANA_CLOUD_PROM_*` |
| A Sentry project, EU org | Unhandled exceptions with the request_id | `SENTRY_DSN` |

Generate the backup keypair **first**, on a machine that is not the VM:

```bash
age-keygen -o cashkit-backup-identity.txt      # keep this file; it is the only way back
grep 'public key' cashkit-backup-identity.txt  # this half goes in the env file
```

A backup you cannot decrypt is not a backup. Store the identity where you will
still have it on the day the VM is gone — that is the day it is for.

---

## 1. The VM

`hcloud` is the CLI; the repository's `hetzner` skill covers it.

```bash
# Staging: the smaller box. CX22 is 2 vCPU / 4 GB.
hcloud server create --name cashkit-staging --type cx22 --image ubuntu-24.04 \
  --location nbg1 --ssh-key luca-mac

# Production.
hcloud server create --name cashkit-prod --type cx32 --image ubuntu-24.04 \
  --location nbg1 --ssh-key luca-mac

# Nothing but 22, 80 and 443 is reachable. Postgres, the service and the
# metrics agent are on the compose network and publish nothing (there is a
# test for that: `test_caddy_config.py::test_only_caddy_publishes_a_port`).
hcloud firewall create --name cashkit-edge
hcloud firewall add-rule cashkit-edge --direction in --protocol tcp --port 22  --source-ips 0.0.0.0/0 --source-ips ::/0
hcloud firewall add-rule cashkit-edge --direction in --protocol tcp --port 80  --source-ips 0.0.0.0/0 --source-ips ::/0
hcloud firewall add-rule cashkit-edge --direction in --protocol tcp --port 443 --source-ips 0.0.0.0/0 --source-ips ::/0
hcloud firewall apply-to-resource cashkit-edge --type server --server cashkit-prod
```

Point the DNS name at the server's IPv4 **before** the first `up`: Caddy's
HTTP-01 challenge fails on a name that does not resolve, and a failed challenge
is rate-limited.

On the box:

```bash
apt-get update && apt-get install -y docker.io docker-compose-v2 git
# Encryption at rest for the volume (SPEC §9): Hetzner encrypts its block
# storage at rest, and the root volume of a Cloud server is on that storage.
# Record which you are relying on; do not assume both.
```

---

## 2. Configuration

```bash
install -d -m 700 /etc/cashkit
cp ops/env.example /etc/cashkit/env
chmod 600 /etc/cashkit/env
$EDITOR /etc/cashkit/env          # fill everything in; no value belongs in git
```

`ops/env.example` lists every variable with a note on what it is for. Every
secret is a `${NAME:?}` interpolation in the compose file, so a missing one
fails at `docker compose up` rather than starting a service with an empty key.

---

## 3. Bring it up

```bash
git clone https://github.com/sushi2all/cashkit.git /srv/cashkit
cd /srv/cashkit

# Staging
docker compose --env-file /etc/cashkit/env \
  -f ops/docker-compose.prod.yml -f ops/docker-compose.staging.yml up -d --build

# Production
docker compose --env-file /etc/cashkit/env \
  -f ops/docker-compose.prod.yml up -d --build

# Migrations are explicit, never on start-up: a service that migrates as it
# boots migrates once per replica and gives a rollback nowhere to stand.
docker compose --env-file /etc/cashkit/env -f ops/docker-compose.prod.yml \
  exec service python -m cashkit_service.migrate
```

Verify, in this order:

```bash
curl -fsS https://$SITE_ADDRESS/healthz                 # {"status":"ok"} — process AND database
curl -fsS -o /dev/null -w '%{http_code}\n' https://$SITE_ADDRESS/metrics   # 404: metrics are not public
docker compose ... exec metrics-agent wget -qO- localhost:9090/-/healthy   # the agent is scraping
```

**And then the one thing no test in the repository can catch** (S5's handoff §8):

```bash
# Upload a workbook through the app, then watch its stream. The frames must
# arrive spread out over the run. If they all appear at the end, the proxy is
# buffering and the import screen is broken while every test still passes.
curl -N -H "authorization: Bearer $TOKEN" \
  "https://$SITE_ADDRESS/imports/$JOB/stream" \
  | while IFS= read -r l; do printf '%s %s\n' "$(date +%s.%N)" "${l:0:80}"; done
```

Executed on the local stack on 2026-08-23: a real 51-second import produced 612
lines with five gaps over a second, each one a model call. A buffered stream
produces no gaps and then everything.

---

## 4. Staging → production

The rule: **production runs an image staging has already run.** Building on the
production box means production is the first machine to discover a broken build.

```bash
# 1. On a build host (or in CI), build and tag from the commit under test.
docker build -f ops/Dockerfile -t cashkit-service:$(git rev-parse --short HEAD) .
docker build -f ops/backup/Dockerfile -t cashkit-backup:$(git rev-parse --short HEAD) ops/backup

# 2. Push both to a registry, or `docker save | ssh … docker load` for a
#    two-VM deployment that does not want a registry yet.

# 3. Deploy that tag to staging by setting it in staging's env file.
CASHKIT_IMAGE=cashkit-service:<sha>
CASHKIT_BACKUP_IMAGE=cashkit-backup:<sha>

# 4. On staging, in this order:
docker compose ... up -d
docker compose ... exec service python -m cashkit_service.migrate
uv run pytest apps/service/trials -m live_model -q     # the model-behaviour gate, against staging
#    plus: the unbuffered-stream check above, by hand.

# 5. Promote the SAME tag to production's env file and `up -d`, then migrate.
```

**Migrations and rollback.** A migration is applied by hand, in its own step,
after the new image is running and before traffic depends on it. Every
migration so far is additive or drops a column nothing reads, so rolling the
image back without rolling the schema back is safe — but that is a property of
these migrations, not a rule of the system. Before adding a migration that is
not backward-compatible with the previous image, write down how to roll it
back, or do it in two deploys.

**Rollback** is setting `CASHKIT_IMAGE` back to the previous tag and `up -d`.
That is why production never runs `--build`.

**Imports in flight are lost on restart**, and that is safe rather than lossy:
the job registry is in-process (D-MLP-83) and an unfinished import applied
nothing, so the user re-uploads. Deploy when nobody is mid-import, or accept it.

---

## 5. Backups and the restore drill

The sidecar runs nightly at 03:15 UTC and prunes at 03:45. To check it without
waiting:

```bash
docker compose ... exec backup /usr/local/bin/backup.sh
docker compose ... exec backup /usr/local/bin/prune.sh
```

**Restore, into somewhere that is not production**, which is the only way to
rehearse it:

```bash
docker compose ... exec \
  -e BACKUP_AGE_IDENTITY_FILE=/age/identity.txt \
  backup /usr/local/bin/restore.sh latest /restore/books cashkit_restored
```

Then open a restored book and compare its closing balances against what the
live one computes. That comparison is the whole point: identical bytes prove a
copy, identical figures prove a book.

The same drill runs end to end, against real containers and a real
S3-compatible store, with:

```bash
uv run pytest apps/service/tests/test_backup_restore_drill.py -m drill -q
```

**Run the restore drill after any change to the backup scripts, and once per
quarter regardless.** A backup system nobody has restored from is a backup
system with an unknown success rate.

---

## 6. Retention (SPEC §9)

The `retention` container sweeps daily at 04:15 UTC — after the backup and its
prune, so the marker it reads is the one this morning's prune wrote. By hand:

```bash
docker compose ... exec retention python -m cashkit_service.retention
```

It prints what it did: model payloads blanked (30 days), link tokens swept,
rotated request logs removed (90 days), deletion backup windows closed, and
**deletion backup windows overdue** — anything above zero there is a §9 breach
in progress and has an alarm on it.

---

## 7. Observability

`ops/observability/` holds the agent's scrape config, the alert rules and the
Alertmanager routing. The rules and the uptime probe belong in Grafana Cloud,
not on the VM: **an alarm that dies with the box is not an alarm.** The local
agent only scrapes and remote-writes.

Drill the alarms — really fire them — with:

```bash
uv run pytest apps/service/tests/test_alarm_drill.py -m drill -q
```

---

## 8. If the box is gone

1. Create a new VM (§1) and point the DNS name at it.
2. Configure it (§2) with the same env file, and the backup identity.
3. `up -d` **without** starting the backup sidecar, so nothing writes a
   snapshot of an empty system over a good one:
   `docker compose ... up -d --scale backup=0`
4. Restore into the live locations:
   `restore.sh latest /var/lib/cashkit/books cashkit`
5. Run the migrations, check `/healthz`, check one book's closing balances
   against whatever the user last saw.
6. Start the backup sidecar.

Step 3 is the one people skip.
