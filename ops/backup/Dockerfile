# The backup sidecar (SPEC §2.2, §9, §12).
#
# It is a separate image from the service on purpose. The service must not hold
# the object-storage credentials or the Postgres superuser role — a container
# that talks to a model provider is the wrong place to keep the keys to every
# book in the product.
#
# Everything it needs and nothing it does not:
#   postgresql17-client  pg_dump / pg_restore, pinned to the server's major
#   git                  `git bundle`, the revision store's own archive format
#   sqlite               `.backup`, an online-consistent copy of the ledger
#   age                  encryption at rest for backups (§9). Public-key, so
#                        this container can write a backup it cannot read
#   aws-cli              any S3-compatible endpoint: Hetzner Object Storage in
#                        production, MinIO in the drill
FROM alpine:3.21

RUN apk add --no-cache \
      bash coreutils tar gzip findutils \
      git sqlite postgresql17-client age aws-cli

COPY backup.sh prune.sh restore.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/prune.sh /usr/local/bin/restore.sh

# Nightly at 03:15 UTC, then prune. `crond -f` keeps the container in the
# foreground so Docker's restart policy is the supervisor.
RUN mkdir -p /etc/crontabs && \
    printf '15 3 * * * /usr/local/bin/backup.sh >> /proc/1/fd/1 2>&1\n45 3 * * * /usr/local/bin/prune.sh >> /proc/1/fd/1 2>&1\n' \
      > /etc/crontabs/root

CMD ["crond", "-f", "-l", "8"]
