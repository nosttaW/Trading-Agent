# Single-host operations

Supported target: one Linux host, Docker Engine, Compose plugin. This is a research/demo deployment—not public multi-user or live-trading infrastructure.

## Deploy

```bash
git clone https://github.com/nosttaW/Trading-Agent.git
cd Trading-Agent
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Store output as APP_ENCRYPTION_KEY in .env; chmod 600 .env
docker compose pull
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/api/readiness
```

Use a TLS reverse proxy for any non-loopback access. Restrict host firewall. Never put `.env`, DB, backups, or logs in Git.

## Logging

FastAPI/Uvicorn emits container stdout/stderr. Configure Docker rotation in `/etc/docker/daemon.json`:

```json
{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"5"}}
```

```bash
sudo systemctl restart docker
docker compose logs --since 1h backend
```

Application audit events: `GET /api/activity`; stored hash-chained in SQLite. This is tamper-evident application data, not immutable WORM storage.

## Backup

Pause writes for a consistent local backup:

```bash
docker compose stop backend
docker run --rm -v trading-agent_strategy_lab_data:/data -v "$PWD/backups:/backup" alpine \
  sh -c 'cd /data && tar czf /backup/strategy-lab-$(date -u +%Y%m%dT%H%M%SZ).tar.gz trading.db*'
docker compose start backend
```

Encrypt externally:

```bash
age -r <RECIPIENT> backups/strategy-lab-*.tar.gz
rm backups/strategy-lab-*.tar.gz
```

Back up `APP_ENCRYPTION_KEY` separately in a secret manager. Without it, provider secrets cannot be recovered. Backups containing provider credentials require encryption and access controls.

## Restore test

Never overwrite the only DB without a verified copy.

```bash
docker compose down
cp -a backups/current-db backups/pre-restore-db
# Decrypt/extract selected archive into the named volume using a temporary container.
docker run --rm -v trading-agent_strategy_lab_data:/data -v "$PWD/restore:/restore:ro" alpine \
  sh -c 'rm -f /data/trading.db* && cp /restore/trading.db* /data/'
docker compose up -d
curl --fail http://127.0.0.1:8000/api/readiness
curl --fail http://127.0.0.1:8000/api/status
```

Then verify session/candidate/result counts through API and test provider decryption with the matching key. Perform quarterly restore tests. Record hashes, operator, timestamps, result.

## Safe update / rollback

Trading is disabled. Preserve the fail-closed state regardless.

```bash
git fetch --all --tags
git checkout <reviewed-commit>
pytest -q
npm ci --prefix frontend && npm run build --prefix frontend
# Set TRADING_AGENT_TAG=<reviewed-commit-sha> in .env.
docker compose pull backend
docker compose stop backend
# Create backup above
docker compose up -d
curl --fail http://127.0.0.1:8000/api/status
```

Rollback:

```bash
docker compose down
git checkout <previous-reviewed-commit>
# Set TRADING_AGENT_TAG=<previous-full-commit-sha> in .env.
# Restore pre-update DB if schema compatibility requires it.
docker compose pull backend
docker compose up -d
```

Never auto-update while any future execution mode is active. Future deployment must halt, cancel no protective exits, reconcile account/orders/positions/cash, reacquire lease, verify hashes/approval/data/risk, then require explicit resume.

## Monitoring

Poll `/api/health` for process health; `/api/readiness` for DB access. Monitor container restarts, disk space, HTTP 5xx, scheduler lag (`next_run_at`), failed/invalid candidates, failed backtests, stale live-data tests, validation jobs stuck in `PENDING`/`RUNNING`, failed validation data coverage, audit-write failures. Current app has no Prometheus endpoint or alert manager; production deployment requires both.
