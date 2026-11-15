# Aiven Runtime deployment

The Runtime deployment uses one application container and a dedicated managed PostgreSQL
database. `Dockerfile.aiven` builds the React frontend and serves it from FastAPI on port
8000. Supervisor runs the API and research worker, restarts failed processes, and stops
the container if a process exhausts its startup retries. An optional Telegram worker is
disabled by default. PostgreSQL holds users, sessions, chats, research runs, browser
state, recurring tasks, and notification queues. Only disposable source caches use the
container filesystem.

Deployment target: organization `org5f46d1473d5`, account `a5f46d1f022e`, project `voco-680f`.

## Connect and deploy

1. Authenticate [Aiven MCP](https://aiven.io/docs/tools/mcp-server). The configured local
   MCP server needs `AIVEN_TOKEN`; this management credential must never be passed to the
   application. Organization policy must allow MCP connections and deployment writes.
2. Confirm organization-admin access to connect GitHub and service-write, project-manager,
   or project-admin access to deploy. A GitHub organization connection requires its owner.
   In Runtime, connect the Aiven Platform GitHub app with access only to
   `markuserkmann/guuey`. Connected repositories are available to other users in the Aiven
   organization. See [GitHub setup](https://aiven.io/docs/products/runtime/connect-github-account).
3. Push the deployment files to the selected GitHub branch. In Console, scan
   `compose.aiven.yaml` and review the `guuey` application and `guuey-db` PostgreSQL service.
   Reuse an existing dedicated database when suitable; do not attach an unrelated database.
   For a Dockerfile deployment through MCP, use repository build context `.` and
   `Dockerfile.aiven`, then connect PostgreSQL with an `application_service_credential`
   integration exposing its connection URI as `DATABASE_URL`.
4. Choose available plans and a common cloud region after checking project services and
   current prices. Start with one application instance. Configure HTTP container port
   **8000**; Runtime provides the public HTTPS endpoint and terminates TLS.
5. Set the variables below before starting the application. Mark passwords, provider
   keys, and session encryption keys as Runtime secrets.

## Runtime variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL URI supplied by the Aiven integration |
| `AIVEN_CA_CERT_BASE64` | Base64-encoded Aiven project CA certificate for database TLS |
| `DATABASE_SSLMODE` | `verify-full`, already set in the image and manifest |
| `AUTH_COOKIE_SECURE` | `true`, already set in the image and manifest |
| `BOOTSTRAP_USERNAME` | Initial account name, for example `admin` |
| `BOOTSTRAP_PASSWORD` | Random initial password, at least 12 characters; store as a secret |
| `GEMINI_API_KEY` | Server-side Gemini credential for chat and research |
| `EXA_API_KEY` | Server-side Exa credential for web research |
| `BROWSER_SESSION_KEY` | Stable 32-byte hex key if saved browser sessions are enabled |
| `APP_PUBLIC_URL` | Generated HTTPS application URL |

The first deployment creates the initial account only if the database has no users.
Later deployments preserve accounts and passwords. An existing valid `AGENT_API_TOKEN`
can be used instead for the shared-workspace mode. Bootstrap credentials are removed
from the API and worker environment after initialization. The Runtime image refuses to
start without PostgreSQL and verified TLS. `LOCAL_DEVELOPMENT=true` permits local database
testing without TLS; leave it unset in Aiven.

To enable Telegram, set `TELEGRAM_ENABLED=true`, `TELEGRAM_BOT_TOKEN`, and
`TELEGRAM_BOT_USERNAME`; set `APP_PUBLIC_URL` for notification links. Users must still link
their accounts and opt individual tasks into delivery. Browser automation requires its
existing provider variables; use the remote browser backend because the image does not
install a local browser engine.

## Verify and redeploy

Verify `/api/health`, sign-in, a chat round trip, and queued worker execution. Redeploy and
check that the account and chat remain. A session-level PostgreSQL advisory lock keeps
only one worker coordinator active while replacement containers overlap. Updates to
GitHub require an explicit [Runtime redeployment](https://aiven.io/docs/products/runtime/deploy-apps).

A new PostgreSQL database starts empty. Local SQLite data is not uploaded or deleted by
this setup; transferring an existing workspace requires a separate data migration.
The original `compose.yaml` continues to run the local SQLite deployment. Setting
`DATABASE_URL` when running the backend directly selects PostgreSQL; without it,
`CHAT_DATABASE_PATH` selects the SQLite file as before.
