# Phase 2: foundations for sharing

Completed in this worktree on 25 November 2026, Pacific/Honolulu. The phase 2 implementation and isolation acceptance checks pass. This is a local, operator-provisioned sharing foundation; public deployment and invitations belong to phase 5. The outstanding phase 1 live-provider, phone-push and Google-consent checks remain open. The original personal database, credentials, Hermes home and running services were not changed or enrolled.

## Acceptance evidence

| Requirement | Implementation and verified evidence |
| --- | --- |
| Individual identities and household roles | Personal 256-bit random sign-in credentials, household memberships, owner/member permissions, owner protection, revocation and multiple-household selection. Actual accounts authenticate through the API and browser; identities are not mocked. |
| Private conversations | Creator-managed participants; household owners cannot bypass another member's private conversation. Message/task routes, native tools, queued work and notification delivery check the same scope. Existing chats remain household-shared. |
| Separate agent contexts and credentials | Dedicated restricted containers for household-shared or private-conversation scope, separate named state volumes, scoped API/tool credentials and container attestation before model routing. Three actual containers passed native memory/session and tool isolation checks. |
| Account-scoped integrations | Read-only Google OAuth with PKCE, expiring session-bound state and one-time exchange, cancellation, scope-bound encrypted tokens, owner-only calendar reads and disconnect. Provider exchanges/calendar responses are mocked in automated tests; real Google consent remains a user-dependent phase 1 check. |
| Migrations and stronger data boundaries | Five ordered, transactional migrations; refusal of unknown revisions or corrupt ancestry; composite participant foreign keys, ancestry triggers and immutable parent scopes. Adoption, repeat application, rollback and simultaneous startup pass on PostgreSQL and SQLite. |
| Background jobs | Atomic claims, one running request per conversation, 120-second leases, 30-second heartbeats, safe expiry and fenced completion. Uncertain model runs become interrupted and are never automatically replayed. Concurrent push senders cannot claim the same live delivery. |
| Authentication and secrets | Hash-only personal/recovery credentials, one-time offline recovery, reauthentication before rotation, session/device revocation, persistent atomic quotas, actual request-stream limits and hosted-mode prerequisites. Integration tokens, runtime API keys and backups use authenticated encryption. |
| Notifications | Account-, session- and device-bound subscriptions. Membership, session expiry and conversation access are checked before enqueue and again before send. Changing accounts retires the browser's previous registration on the server even without JavaScript cleanup. |
| Usage | Per-run/per-account token records and atomic household daily request reservations across workers. Limits count top-level requests, not dollars or every delegated provider call. |
| Backup and restore | Encrypted snapshots restored into fresh SQLite and PostgreSQL destinations, preserving records and encrypted integration credentials. A stopped native home was exported and restored into a fresh container volume with memory, sessions and learned skills intact and new runtime credentials. |

The final suite passes **66 tests on SQLite and the same 66 on PostgreSQL 17**, plus Ruff and whitespace checks. PostgreSQL used a disposable container and fresh schemas per test, including a PostgreSQL-to-PostgreSQL restore drill. Headless Chrome passed desktop/mobile conversation controls and actual browser sign-in, code rotation and offline recovery. All test accounts, messages, keys and calendar fixtures were synthetic. No paid model calls, real push deliveries or real Google account authorization occurred.

## Authentication and membership

Accounts use operator-generated high-entropy personal codes rather than user-chosen passwords. This avoids a public signup/password/email-verification system for the bounded pilot. The CLI writes the personal code and a separate offline recovery code once into a new mode-0600 file. PostgreSQL stores hashes only. Keep the recovery code separately; using it rotates both codes, revokes all sessions and removes device registrations. The signed-in account UI can also rotate codes after confirming its current personal code. An operator can recover an account locally using reset-account. There is no email delivery or self-service signup.

The first account in a household must be an owner. Enrollment immediately disables shared-code browser sign-in and invalidates legacy sessions for that household without rotating existing credentials. Owners can list/revoke members, and the last owner cannot be removed or demoted. The trusted operator can add an existing account to another household or change a membership role. Signing in to an account with several memberships shows a household selector; changing households never automatically grants access to private conversations.

All browser mutations require the configured Origin. Session/device cookies are HttpOnly and use SameSite=Lax so Google's top-level OAuth callback can carry the session cookie; the callback also requires session-bound state. Hosted mode requires HTTPS, secure cookies, PostgreSQL, personal login and a private encryption key. It disables the legacy household tool credential and the local single-runtime fallback. Local legacy mode remains available for the existing personal POC; it is not the configuration for sharing with friends.

Persistent fixed-window quotas cover sign-in attempts, recovery, messages, OAuth actions, calendar reads and tool authentication. Quotas use hashed identities and ASGI client addresses, not arbitrary application-parsed forwarding headers. Configure trusted proxy addresses correctly when hosting. The API also caps the actual request stream at 8 KiB, including chunked requests, and hides SQL parameters in database exception representations. The supplied CLI disables HTTP access logging so OAuth callback query strings are not printed; a future reverse proxy needs the same treatment.

```sh
uv run python -m mental_loader.cli init-secrets
uv run python -m mental_loader.cli migrate

uv run python -m mental_loader.cli create-account \
  --household-id HOUSEHOLD_UUID --name 'Your name' --role owner \
  --output .local/accounts/owner.json

uv run python -m mental_loader.cli create-account \
  --household-id HOUSEHOLD_UUID --name 'Another member' --role member \
  --output .local/accounts/member.json

# Trusted local operator actions; each credentials output must be a new private file.
uv run python -m mental_loader.cli add-member \
  --household-id HOUSEHOLD_UUID --user-id EXISTING_USER_UUID --role member
uv run python -m mental_loader.cli reset-account \
  --user-id USER_UUID --output .local/accounts/recovered.json
```

## Conversation and notification rules

Existing and default new chats are household-shared. A personal account can create a private chat, then add selected current household members through the UI. Sharing grants access to the saved messages and reminders. Only the creator manages participants; household owner status does not override private chat permissions. Visibility and creator identity cannot currently be changed. Removing a creator's membership can leave an inaccessible chat; ownership transfer and deletion policy are future product work rather than a hidden access override.

Unconfigured private chats support deterministic /remind commands and explain that AI is unavailable. A private chat can use natural-language AI only after its own isolated runtime is provisioned and registered. Its messages never fall back to the household runtime. The UI reports whether that chat has a configured AI route. Integration data is not silently copied into shared chat or Hermes memory.

Private reminders appear only to current participants. Shared household runtime credentials cannot list private tasks, create reminders in private chats or retrieve private receipts by replaying a request ID. Private runtime credentials are bound to one conversation and cannot dispatch household jobs. A shared runtime's native no-agent cron performs deterministic delivery for its household, including private reminders; it does not expose their contents to an agent. Notification payloads remain generic, and the saved-chat link is checked again on opening.

Queued requests are checked again before execution. Revoked access cancels pending work. Already-running external operations are not forcibly undone. Leases distinguish live workers from expired executions; a new process does not interrupt another live worker. Expired model work becomes interrupted instead of being replayed. Push transport remains at least once after an uncertain expired send, uses a stable notification tag, and stops retrying after three claims. Accepted sends still do not prove iPhone display.

## Isolated Hermes runtimes

The runtime image is built from the exact hermes-runtime.ref revision plus the reviewed compatibility patch. The builder archives committed source only; personal .env files, homes, runtime state and Git metadata are excluded. The base image and uv version are pinned. The bundled SQLite version triggers Hermes' native safe rollback-journal fallback; the smoke checks observed that fallback rather than enabling unsafe WAL. No pinned Hermes upgrade was performed.

Each runtime runs as UID/GID 65532 with a read-only root, all capabilities dropped, no-new-privileges, bounded processes/CPU/memory, a small temporary filesystem and exactly one private named home volume. It has no application source/data mounts or Docker socket. API ports bind only to host loopback. Runtime skills and native storage live in that scope's home. Delegation starts from a restricted household/memory/session/skills toolset; it does not inherit host tools or MCP access. The application retains trusted Docker access for attestation; the model containers do not.

Registration requires an active household owner, a matching private creator when applicable, a correctly labeled live container, a compliant launch configuration, a distinct API port and a state volume not used by another registered scope. Both container and volume carry a daemon-owned scope label; a previously used volume cannot be silently attached as another household or private context. API keys are encrypted with their scope as associated data; tool credentials are hashed. Before each model request the app rechecks the registered container. A down or changed registered container fails the request instead of rerouting its input. Removing a runtime route invalidates its backend tool capability immediately. The Docker daemon and host operator remain trusted, as they can administer all containers; this is not a claim of resistance to host/root compromise.

```sh
# Build from an existing pinned checkout, without changing that checkout.
uv run python scripts/build_runtime_image.py --runtime /path/to/pinned/hermes-runtime

# backend-url must be trusted HTTPS reachable from the container.
# provider-env is optional; it may supply provider API keys, never gateway/tool credentials.
uv run python scripts/runtime_box.py \
  --household-id HOUSEHOLD_UUID --backend-url https://YOUR_BACKEND \
  --output .local/runtimes/household.json

# Add --conversation-id PRIVATE_CHAT_UUID for a private conversation runtime.
uv run python -m mental_loader.cli register-runtime --manifest .local/runtimes/household.json
uv run python -m mental_loader.cli revoke-runtime --scope household:HOUSEHOLD_UUID
```

Provisioning creates a new container, volume and private manifest. It does not register a model route or make a model request. Shared runtimes receive exactly one native every-minute no-agent reminder job; private runtimes do not receive a household dispatcher. Registration is a separate operator action. No real household has been moved into this topology by the implementation work.

## Google Calendar and secrets

Generate the application key once with init-secrets and preserve it separately from backups. The key is not stored in PostgreSQL or mounted into agent containers. Integration tokens and runtime API keys use AES-GCM with tenant/account/provider or runtime scope as associated data, so moving ciphertext between owners fails authentication. Existing plaintext household Google probe files are not automatically imported or reused for another account.

For browser linking, place a Google Web OAuth client JSON at GOOGLE_WEB_CLIENT_PATH, keep it private, and register PUBLIC_ORIGIN/api/integrations/google/callback as its exact redirect URI. A personal account can then connect, read its primary calendar and disconnect through the app. Authorization requests use PKCE and ten-minute session-bound state. Exchange is claimed once; another user or a new session cannot complete it. Disconnecting while an exchange is in flight prevents it from recreating the connection. Only the signed-in account can read/decrypt its connection, including another account within the same household. Revoked membership prevents further reads or token storage.

The desktop read-only probe now also requires --user-id and stores credentials encrypted against that membership. Neither path writes calendar events or implements event sync; birthday discovery/update handling belongs to phase 3. Real authorization still needs the user's Google client and consent.

## Backup and recovery

Quiesce the app workers and Hermes gateways before a coordinated snapshot. For local Hermes homes, pass --home. For container homes, stop each scoped container and pass its private --runtime-manifest; the exporter refuses a running container. Include every relevant home. A database-only backup is possible but does not contain native agent state.

```sh
uv run python -m mental_loader.cli backup --output .local/backups/pilot.enc \
  --home .local/hermes-home

# Alternatively/additionally, repeat this flag for each stopped scoped runtime:
uv run python -m mental_loader.cli backup --output .local/backups/containers.enc \
  --runtime-manifest .local/runtimes/household.json

# Supply a fresh destination via the environment; do not put passwords in CLI arguments.
# RESTORE_DATABASE_URL must be set locally before this command.
uv run python -m mental_loader.cli restore --input .local/backups/pilot.enc \
  --homes-output .local/restored-homes
```

Snapshots encrypt application records and included native files. SQLite stores are copied using the SQLite backup API so committed WAL content is included. Restore validates the encrypted manifest, refuses populated database destinations and existing home directories, and rejects unsafe archive paths/symlinks. It restores into a fresh schema and privately staged homes; a database failure removes the newly staged home output. The app encryption key, Google client configuration, VAPID identity and deployment environment must be preserved separately.

Restored browser sessions, device subscriptions, OAuth attempts and runtime routes are not reactivated. Running model work becomes interrupted; uncertain push sends return to the outbox but have no restored device recipients. Reauthenticate and enable notifications again. Scoped container exports carry ownership metadata rewritten from the daemon label; --restore-home rejects another household/conversation scope. Re-provision runtime containers using --restore-home with that scope's restored directory, then re-register their new manifests; API/tool credentials and volumes are regenerated while native memory, sessions and learned skills are preserved. Keep provider settings/credentials private and configure them deliberately in the new home. Legacy local homes without scope metadata can be restored as local files but are not automatically adopted into the sharing topology.

## Reproduce verification

```sh
uv run pytest -q
uv run ruff check .

# Use only a disposable database. Tests create/drop fresh ml_test_<random> schemas.
MENTAL_LOADER_TEST_POSTGRES_URL=postgresql+psycopg://USER:PASSWORD@127.0.0.1:PORT/TEST_DB uv run pytest -q

uv run python scripts/conversation_browser_smoke.py
uv run python scripts/container_isolation_smoke.py
uv run python scripts/runtime_gateway_smoke.py
```

The container checks require the built mental-loader-hermes:phase2 image and Docker access. They create and remove synthetic containers/volumes and use no personal provider environment. Browser screenshots contain synthetic data only under ignored .local/screenshots/phase2. The original separate-process native smoke remains available as scripts/hermes_isolation_smoke.py.

## Next work

Phase 3 is the birthday/gift workflow: recipients, interests, budgets, dates, preparation deadlines and completion, followed by account-authorized calendar discovery/update handling. Before a public friends pilot, complete the outstanding phase 1 live checks and phase 5 hosting work: HTTPS/domain setup, provider budgets, reverse-proxy configuration, operational backup scheduling, restore procedures and deployment monitoring. Operator-created personal codes are the implemented identity policy; public signup, email recovery and an external identity provider are not required for this local phase 2 acceptance and have not been silently introduced.

Implementation references: [Google's web-server OAuth flow](https://developers.google.com/identity/protocols/oauth2/web-server), [Docker security boundaries](https://docs.docker.com/engine/security/) and [PostgreSQL locking](https://www.postgresql.org/docs/17/explicit-locking.html). Repository tests and smoke outputs, rather than those references, are the evidence for the implementation claims above.
