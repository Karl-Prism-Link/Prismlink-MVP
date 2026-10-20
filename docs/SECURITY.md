# Security notes

## Required secrets

The API refuses to start unless these are set to independent random values of at
least 32 characters (see `core/config.py`). This applies to every environment
except `APP_ENV=test`:

| Variable | Purpose |
| --- | --- |
| `JWT_SECRET_KEY` | Signs access tokens |
| `CREDENTIAL_ENCRYPTION_KEY` | Encrypts stored Google OAuth tokens |
| `OAUTH_STATE_SECRET` | Signs Google OAuth `state` values |

Optional but recommended:

| Variable | Purpose |
| --- | --- |
| `AURORA_EVENT_TOKEN` | Bearer token for `/internal/sip-events`; the endpoint returns 503 while unset |
| `METRICS_TOKEN` | Bearer token for `/metrics`; production returns 404 while unset |
| `TRUST_PROXY_HEADERS` | Set only behind a trusted reverse proxy that appends `X-Forwarded-For` |

Generate values with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

A fresh checkout copied from `.env.example` will not boot until these are filled
in. That is intentional: the previous defaults were publicly known.

## Committed database file (action required)

`prism_link.db.pre-a587902-20260920` was committed in `01a000c` and deleted in
`226f7dc`. It is gone from the current tree but still retrievable from history:

```bash
git show 01a000c:prism_link.db.pre-a587902-20260920 > /tmp/recovered.db
```

Treat every credential and personal record it contains as exposed:

1. Rotate `JWT_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, `OAUTH_STATE_SECRET`,
   `AURORA_EVENT_TOKEN`, `METRICS_TOKEN` and any provider keys.
2. Force-reset passwords for accounts present in that snapshot.
3. Re-authorise Google Calendar connections so old refresh tokens are revoked.
4. Purge history with `git filter-repo --path prism_link.db.pre-a587902-20260920 --invert-paths`
   (or BFG), then force-push and ask every collaborator to re-clone.
5. Keep the repository private until steps 1-4 are complete.

`tests/test_repository_hygiene.py` fails if a database, key or `.env` file is
tracked again.

## Operational notes

- `/metrics` is token-protected. `prometheus.yml` documents the `credentials_file`
  setup; the token file should be mode `0600`.
- Rate limiting is in-process and keyed on client address plus, for login, the
  submitted email. A multi-worker or multi-instance deployment still needs a
  shared store such as Redis (`REDIS_URL`) for consistent limits.
- The dashboard keeps its access token in `sessionStorage`, so a successful XSS
  can still read it. Serve the dashboard from a CSP that disallows inline script
  once the inline handlers are moved to a bundled file.
