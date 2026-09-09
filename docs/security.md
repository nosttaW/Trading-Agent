# Security model

## Enforced now

- AI credentials originate server-side. Fernet authenticated encryption uses deployment-managed `APP_ENCRYPTION_KEY`.
- Saved keys/custom headers return masked/boolean status only. Authorization content never enters audit payloads.
- Remote AI endpoint default: HTTPS/443. DNS resolves before call. Private, loopback, metadata/link-local, reserved, multicast, credentials-in-URL, redirects, unsafe headers blocked.
- Explicit local exception: administrator `host:port` allowlist. No browser-controlled bypass.
- Provider output receives strict local schema validation. Unsupported families/variants remain invalid attempts.
- Generated source is display/download only. Trusted engine executes reviewed template IDs.
- SQL parameters used throughout. Pydantic rejects unknown configuration fields. JSON replaces unsafe pickle.
- SQLite transitions commit transactionally. UTC timestamps. Decimal used for financial accounting/fills.
- Audit records form an application-level SHA-256 chain.
- CORS origin restricted by deployment setting. No telemetry.
- Broker submission function absent. `/api/orders` fails `403`. Paper/live status stays disabled.

## Deliberately disabled

### Hostile Python

No suitable hostile-code sandbox is deployed. Ordinary containers and AST/import checks are not treated as sufficient. Arbitrary AI/uploaded Python execution remains disabled. A future implementation needs microVM/gVisor-class isolation, no network/credentials/host FS, read-only root, non-root user, seccomp, process/CPU/memory/output/time limits, fixed dependencies, per-strategy failure isolation, and escape testing.

### Broker paper/live

No Alpaca credentials, account calls, orders, cancel, replacement, streaming, or reconciliation exist. Broker paper cannot substitute for app simulation. Live cannot substitute for paper. Before implementation:

- authentication and ownership;
- secure sessions, CSRF, stronger live reauthentication;
- secret rotation/deletion and external secret manager;
- independent risk and broker processes without AI access;
- account/mode identity checks;
- approval hash/version/account/instrument/limits/schedule/expiry binding;
- persistent intent/client order IDs;
- unknown-outcome reconciliation before retry;
- partial fill, buying power, pending exposure, local/broker state reconciliation;
- single-executor lease, clock/data freshness, restart HALTED state;
- append-only external/WORM audit storage;
- Emergency Stop policies for entries, cancellations, protective exits, explicit liquidation;
- required paper/forward evidence; 24-hour initial, seven-day maximum renewal.

## Production gaps

Current app is local single-user software. It does not yet implement authentication, resource ownership, CSRF sessions, PostgreSQL multi-instance leases, hardened reverse proxy headers, external KMS, egress firewall, encrypted DB volume, WORM audit storage, remote market-data ingestion, alerting, or independent risk supervisor. Do not expose publicly or trade with it.

DNS validation has a resolution/request TOCTOU window because `httpx` resolves independently. Production must pin validated addresses through a controlled egress proxy/resolver and revalidate every redirect (redirects currently rejected).

Fernet protects application secrets at rest but not a host compromised together with its encryption key. Keep the key outside the image/database, restrict access, rotate via decrypt/re-encrypt maintenance, and back it up separately.
