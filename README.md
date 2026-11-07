# Flowwolf

[![CI](https://github.com/WolfcastleDE/flowwolf/actions/workflows/ci.yml/badge.svg)](https://github.com/WolfcastleDE/flowwolf/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/WolfcastleDE/flowwolf)](https://github.com/WolfcastleDE/flowwolf/releases)
[![Image](https://img.shields.io/badge/image-ghcr.io%2Fwolfcastlede%2Fflowwolf-2f5bea)](https://github.com/WolfcastleDE/flowwolf/pkgs/container/flowwolf)
[![License: MIT](https://img.shields.io/badge/license-MIT-16181d)](LICENSE)

Workflow automation, without the weight. Flowwolf is an open source automation server written in Rust.
It reads and writes the n8n workflow format, runs n8n expressions and Code nodes in an embedded
JavaScript sandbox (QuickJS) and ships as one binary with the editor and website built in.

| | |
|---|---|
| Docker image | ~105 MB (distroless, non-root, amd64 + arm64) |
| Memory at idle | ~5–10 MB |
| Storage | one SQLite file + encryption key in `/data`, or PostgreSQL (`DB_TYPE=postgresdb`) |

## Quick start

```bash
docker run -d --name flowwolf -p 5678:5678 -v flowwolf_data:/data ghcr.io/wolfcastlede/flowwolf:latest
```

Or with Compose: download [`docker-compose.yml`](docker-compose.yml) and run `docker compose up -d`.

- Website: http://localhost:5678/
- Editor: http://localhost:5678/app (the first visit creates the owner account)
- Production webhooks: `/webhook/<path>`, test webhooks: `/webhook-test/<path>`, forms: `/form/<path>`
- Public API: `/api/v1/...` with header `X-N8N-API-KEY` (create keys under Settings)

## Deploy

Flowwolf is one container with one volume, so any Docker host or PaaS works:

- **Docker Compose:** the [`docker-compose.yml`](docker-compose.yml) in this repository (also attached to every release).
- **Dokploy / Coolify / Portainer:** create a Compose service from this repository (or the image
  `ghcr.io/wolfcastlede/flowwolf:latest`), add a domain pointing to port **5678** and set
  `FLOWWOLF_URL=https://<your-domain>` so webhook, form and invitation links use the public address.
- **Binary:** every release has Linux builds for x86_64 and aarch64; run `./flowwolf` (data goes to `./data`).

Keep the `/data` volume and `FLOWWOLF_ENCRYPTION_KEY` in your backups. Releases are tagged `vX.Y.Z`;
images are published as `:X.Y.Z`, `:X.Y` and `:latest`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FLOWWOLF_PORT` (`N8N_PORT`) | `5678` | HTTP port |
| `FLOWWOLF_DATA_DIR` | `./data` (`/data` in Docker) | Database and key location |
| `FLOWWOLF_URL` (`WEBHOOK_URL`) | `http://localhost:<port>` | Public base URL for webhook/form links |
| `FLOWWOLF_ENCRYPTION_KEY` (`N8N_ENCRYPTION_KEY`) | generated into `encryption.key` | Credential encryption |
| `GENERIC_TIMEZONE` | `UTC` | Schedules, `$now`, date nodes |
| `FLOWWOLF_FILES_DIR` | `<data>/files` | Only directory the file nodes may touch |
| `FLOWWOLF_API_BASE_OVERRIDES` | – | `https://api.x.com/v2=http://mock/x,...` points fixed integration hosts at a sandbox, proxy or mock |
| `FLOWWOLF_ENABLE_EXECUTE_COMMAND` | `false` | Allow the Execute Command node |
| `FLOWWOLF_BLOCK_ENV_ACCESS` | `false` | Hide `$env` from expressions |
| `FLOWWOLF_CODE_TIMEOUT_MS` | `60000` | Code node time limit (JavaScript and Python) |
| `FLOWWOLF_CONCURRENCY_PRODUCTION_LIMIT` (`N8N_…`) | unlimited | Max. parallel production executions; the rest is queued |
| `EXECUTIONS_MODE` (`FLOWWOLF_…`) | `regular` | `queue`: production runs go to `flowwolf worker` processes (same volume with SQLite, any host with PostgreSQL) |
| `DB_TYPE` | `sqlite` | `postgresdb` stores everything in PostgreSQL (n8n variables): `DB_POSTGRESDB_HOST`, `_PORT` (5432), `_DATABASE`, `_USER`, `_PASSWORD`, `_SCHEMA` (`public`), `_SSL_ENABLED`, `_SSL_REJECT_UNAUTHORIZED` |
| `FLOWWOLF_WORKER_CONCURRENCY` | `10` | Parallel executions per worker |
| `FLOWWOLF_EXECUTIONS_MAX_AGE_HOURS` / `_MAX_COUNT` | `336` / `10000` | Execution pruning |

## What is implemented

**Engine:** n8n v1 execution order (depth first), multi-input waiting (Merge), loops (Loop Over Items),
pinned data, partial runs up to a node ("Test step"), retry on fail, `alwaysOutputData`, `executeOnce`,
on-error modes (stop / continue / error output), sub-workflows, error workflows, workflow static data,
cancellation, crash recovery, execution pruning.

**Expressions & Code:** `{{ }}` templates, `$json`, `$input`, `$('Node')`, `$node`, `$items`, `$now`, `$today`,
`$vars`, `$env`, `$execution`, `$workflow`, `$getWorkflowStaticData`, Luxon-style `DateTime`, n8n helper methods
(`.sum()`, `.isEmpty()`, `.toTitleCase()`, ...). Code node in both modes with `console.log` streamed to the browser.

**Nodes (n8n type names):** Manual, Schedule (+ legacy Cron/Interval), Webhook, Form, Email Trigger (IMAP/IMAPS),
Workflow Trigger (on activation/update), MQTT Trigger, Redis Trigger (pub/sub, patterns), RSS Feed Trigger,
Local File Trigger (inside the files directory), Execute Workflow Trigger,
webhook-registering triggers (activation creates the webhook at the service, deactivation deletes it; handles
GitHub ping, Asana handshake and HEAD/GET checks; events are verified against a per-trigger secret for GitHub, GitLab,
Stripe, Typeform, Calendly, Telegram, Asana, ClickUp, Linear, WooCommerce, Bitbucket, MailerLite and Cal.com — unsigned or
forged events get 401, Stripe/Calendly signatures older than 5 minutes are refused): GitHub, GitLab, Stripe, Shopify, Typeform, Calendly, Telegram, Trello, Jira,
Asana, ClickUp, Linear, Mailchimp, Pipedrive, Webflow, WooCommerce, Bitbucket, Netlify, Box, Formstack, SurveyMonkey, Postmark,
MailerLite, ActiveCampaign, GetResponse, Lemlist, Mautic, Cal.com, Gumroad, Figma, Keap, KoBoToolbox, Onfleet, PayPal, Webex, Zendesk,
Copper, Affinity, Eventbrite, Workable, Acuity Scheduling, Brevo, Mailjet and Help Scout Trigger (Webex and Help Scout verified with
HMAC-SHA1); polling triggers that remember what they have seen and start one run per new entry: Gmail, Google Sheets, Google Drive,
Google Calendar, Airtable, Notion, Microsoft Outlook, Clockify and Toggl Trigger (n8n `pollTimes`),
Error Trigger, HTTP Request (all auth types, query/header/body modes, files, batching), Code, Edit Fields (Set v1–v3),
If (v1/v2), Filter, Switch, Merge, Loop Over Items, Wait, No-Op, Stop and Error, Execute Workflow,
Respond to Webhook, Split Out, Aggregate, Sort, Limit, Remove Duplicates, Summarize, Rename Keys, Date & Time,
Crypto, HTML extract, Extract from File, Convert to File, Read/Write Files, RSS Read, Send Email (SMTP),
Postgres (with TLS), Redis, Slack, Telegram, Discord, OpenAI (and compatible APIs), Anthropic, Execute Command,
GitHub, GitLab, Notion, Airtable, Baserow, Supabase, Stripe, Shopify, WooCommerce, Pipedrive, HubSpot, Mailchimp,
SendGrid, Brevo, Mailgun, Todoist, ClickUp, Asana, Linear, Monday.com, Trello, Jira, Freshdesk, Google Sheets,
Google Drive, Gmail, Google Calendar, Microsoft Outlook, Microsoft Teams, WhatsApp Business, Twilio, Pushover,
ntfy, Mattermost, Webflow, Typeform, OpenWeatherMap, Postmark, Resend, Vonage, Rocket.Chat, Matrix, Zulip, Gotify, Mautic, ActiveCampaign, Kit, Intercom, Calendly, Zoom, Dropbox, Strapi, WordPress, Ghost, Contentful, Sentry, PagerDuty, Datadog, Cloudflare, Hetzner Cloud, DigitalOcean, Netlify, Vercel, Jenkins, Home Assistant, Google Gemini, DeepL, Hugging Face, Bitly, CoinGecko, Hacker News, sevDesk, lexoffice, MailerLite, Mistral AI, Groq, Perplexity, Ollama, Google Translate, Google Tasks, Google Docs, Google BigQuery, YouTube, Microsoft To Do, Microsoft OneDrive, Clockify, Toggl Track, Harvest, Elasticsearch, Firebase, Mastodon, Discourse, Bitbucket, Zammad, Raindrop, Plausible, Grafana, MySQL/MariaDB, X (Twitter), LinkedIn, Reddit, Spotify, Box, Facebook Graph API, Coda, NocoDB, Grist, MQTT, Shopware 6 (Admin API), Confluence, ServiceNow, Zendesk, Mailjet, PostHog, Amplitude, CircleCI, Jotform, Bannerbear, Paddle, Mollie, Salesforce, Zoho CRM, Xero, QuickBooks Online, Hunter, Brandfetch, Lemlist, Customer.io, Segment, Iterable, GetResponse, Pushbullet, Pushcut, SIGNL4, YOURLS, Storyblok, Travis CI, Rundeck, Splunk, TheHive 5, MISP, Gong, HighLevel, Medium, Wise, Onfleet, Peekalink, Marketstack, OpenThesaurus, Oura, AWS (Lambda, SNS, SQS, SES, DynamoDB, Comprehend, Rekognition, Textract, Transcribe — Signature V4), Freshservice, Copper, Agile CRM, Monica CRM, KoBoToolbox, Formstack, Wufoo, SurveyMonkey, Tapfiliate, Dropcontact, MessageBird, Plivo, Webex, ERPNext, Magento 2, Bubble, Quick Base, Phantombuster, Sendy, Mocean, Google Perspective, Disqus, SecurityScorecard, urlscan.io, Strava, Google Contacts, Google Slides, Google Chat, Google Cloud Storage, Google Analytics (GA4), Google Books, Google Cloud Natural Language, Cloud Firestore, Google Workspace Admin, Excel 365, Microsoft Entra ID, SharePoint, AWS Cognito, AWS Certificate Manager, Affinity, APITemplate.io, Beeminder, BambooHR, Bitwarden, Chargebee, Cockpit, Demio, DHL, Drift, Freshworks CRM, GoTo Webinar, Help Scout, Invoice Ninja, Keap, Mailcheck, Mandrill, NASA, npm, Okta, PayPal, PostBin, Salesmate, Stackby, SyncroMSP, Twist, Uplead, UptimeRobot, Vero, Compare Datasets, JWT, TOTP, XML, Markdown, Compression (zip/gzip), AWS S3 (and S3-compatible storage), MongoDB, FTP/SFTP, Execution Data, Debug Helper, Flowwolf/n8n API — 408 node types and 266 credential types in total.

**AI:** AI Agent node with tool calling (OpenAI-compatible APIs incl. Ollama, or Anthropic), workflows and HTTP
endpoints as tools, per-session conversation memory; Chat Trigger with a hosted chat page (`/chat/<path>`).

**Wait node:** time interval, specific time, or resume on `$execution.resumeUrl` (webhook) /
`$execution.resumeFormUrl` (form) with an optional time limit. Webhook/form waits and time waits over 65 s
park the execution in the database (full engine state: pending branches, merge inputs, loop state) and free
the thread; they resume after restarts, in the same execution.

**OAuth2:** generic `oAuth2Api` (authorization code, PKCE and client credentials) plus presets for Google, Microsoft, X, LinkedIn, Reddit, Spotify, Box, Zoom and Dropbox;
"Connect account" in the credential dialog, tokens are refreshed automatically and never sent to the browser.

**Python:** the `flowwolf:python` image (`docker build --build-arg FEATURES=python -t flowwolf:python .`, 110 MB)
adds Python 3 to the Code node (`_input.all()`, `_json`, `item.json.field`, `print`). It runs on an embedded
RustPython VM. On Linux every run happens in its own runner process (`flowwolf python-sandbox`) with an empty
environment, a memory limit (`N8N_RUNNERS_MAX_MEMORY_MB`, default 1024) and a seccomp filter installed after start-up:
opening files, sockets, exec, fork and signals to other processes fail with EPERM. Every user may run Python there;
on other platforms Python runs in-process without isolation and stays limited to owners and admins.

**AI cluster nodes (n8n format):** the AI Agent and the chains (Basic LLM, Sentiment Analysis, Text Classifier,
Information Extractor, Summarization, Question and Answer) take sub-nodes through `ai_*` connections exactly as n8n stores them:
chat models (OpenAI, Anthropic, Ollama, Mistral, Groq, Gemini, DeepSeek, OpenRouter, xAI, Cohere, Azure OpenAI, AWS Bedrock),
memory (simple, Postgres, Redis), tools (calculator, code, HTTP, workflow, think, Wikipedia, SerpApi, Wolfram|Alpha, SearXNG,
vector store, MCP client over streamable HTTP), output parsers (structured, item list, auto-fixing), embeddings (OpenAI,
Ollama, Mistral, Gemini), Default Data Loader (JSON or files: text, CSV, JSON, PDF) with text splitters, vector stores
(Simple, Qdrant, Pinecone, Supabase, Postgres PGVector) and the vector store retriever. The MCP Server Trigger offers the
tools connected to it at `/mcp/<path>` (streamable HTTP) and `/mcp/<path>/sse` (HTTP+SSE), with optional bearer/header
auth; the MCP Client Tool speaks both transports too. In the editor sub-nodes hang below their root; "+ Model",
"+ Memory", "+ Tool" … buttons under a root add and connect them. Not yet: vector stores beyond the five above.

**RAG:** the Vector Store node embeds text with any OpenAI-compatible embeddings API (OpenAI, Ollama, …),
stores chunks in SQLite and returns the most similar ones; the AI Agent can use a store as a tool.

**Sharing:** every workflow and credential has an owner and can be shared with other users as *viewer*
(read-only, incl. executions) or *editor* (edit and run). Deleting and sharing stay with the owner. Members only
see their own and shared resources; executions only use credentials the workflow owner may access.

**Queue mode:** with `EXECUTIONS_MODE=queue` the main process (editor, triggers, webhooks) only queues
production executions; any number of `flowwolf worker` processes claim them atomically, run them and hand
webhook responses back through the database. With SQLite the workers share the data volume; with PostgreSQL
(`DB_TYPE=postgresdb`) they can run on any host that reaches the database. Every process needs the same
`FLOWWOLF_ENCRYPTION_KEY`. Jobs are claimed with `FOR UPDATE SKIP LOCKED`, so no run is taken twice. Stop works across processes. See the commented
`worker` service in `docker-compose.yml`.

**Folders:** workflows can be organised in folder paths (`Sales/Leads`), filtered and moved in the list.

**Source control:** Settings → Source control pushes workflows (n8n JSON), credential stubs without secrets and
variable keys to a Git repository and pulls them into other instances (dev → prod). Two transports:
plain **Git over SSH** to any server (GitHub, GitLab, Gitea, Bitbucket, self-hosted): Flowwolf generates an ed25519
deploy key, you add the public key with write access, the server host key is pinned on first contact; or the
GitHub / Gitea contents API with a token. Only changed files are committed; branches can be listed and chosen.
Builds without the default cargo feature `git-ssh` drop libgit2/libssh2 and keep only the API transport.

**Users and roles:** owner, admin and member. Admins invite users by email (or hand out the link when no SMTP
is set up via `N8N_SMTP_HOST`, `_PORT`, `_USER`, `_PASS`, `_SENDER`, `_SSL`), change roles, copy password reset
links and delete users with a transfer of their workflows and credentials. Everyone can reset a forgotten password,
change their own (other sessions end) and turn on two-factor authentication (TOTP with ten one-time recovery codes).
Members see only their own and shared resources.

**Platform:** Argon2 passwords, AES-256-GCM credentials (secrets masked in the API), personal API keys, variables,
live execution events (SSE), executions browser with retry, import/export of n8n JSON, copy/paste of nodes between
Flowwolf and n8n.

## Adding integrations

Most SaaS nodes are declarative: describe resources, operations and fields in `tools/gen_integrations.py`,
run `python tools/gen_integrations.py`, rebuild. The executor in `src/nodes/declarative.rs` handles paths,
query/body mapping, auth (bearer, basic, header, query, OAuth2), pagination limits and response roots.

## Not (yet) covered

n8n ships 400+ integrations; Flowwolf has 408 node types (Slack, Telegram, Discord, GitHub, Gmail, Google Drive, AWS S3, MongoDB and FTP/SFTP carry n8n's operation sets — S3, MongoDB, FTP and SFTP are tested against real servers; many others still have fewer operations per service than n8n) — everything else goes through the HTTP Request node
with stored credentials (including OAuth2).

## Development

Everything at once (builds both images, starts Postgres, Redis, MariaDB and Gitea, runs all suites on fresh containers):

```bash
tests/run_all.sh
```


No local toolchain needed — cargo runs inside the official Rust image:

```bash
./dev.sh test                                    # unit tests
docker run -d --rm --name fw-e2e -p 5679:5678 flowwolf:latest
python tests/e2e.py http://localhost:5679        # end-to-end checks against a fresh instance
# integrations: needs Postgres on 5433 (plain) / 5434 (TLS) and Redis on 6380 reachable from the container
python tests/e2e_integrations.py http://localhost:5679 host.docker.internal
python tests/e2e_ai_share.py http://localhost:5679   # agent, chat, templates, sharing (fresh instance)
# vector store, queue and Python: fresh flowwolf:python container with N8N_CONCURRENCY_PRODUCTION_LIMIT=1
python tests/e2e_rag_python.py http://localhost:5679
python tests/e2e_triggers.py http://localhost:5679 host.docker.internal   # MQTT (Mosquitto :1883), Redis :6380, RSS, local files, Shopware
# AI cluster nodes: Postgres 5433, pgvector Postgres 5436, Redis 6380, Qdrant 6333; instance with the Pinecone overrides
docker run -d --rm --name fw-e2e -p 5679:5678 -e FLOWWOLF_API_BASE_OVERRIDES="$(python tests/e2e_ai_cluster.py --overrides)" flowwolf:latest
python tests/e2e_ai_cluster.py http://localhost:5679 host.docker.internal
# polling triggers against growing fake sources
docker run -d --rm --name fw-e2e -p 5679:5678 -e FLOWWOLF_API_BASE_OVERRIDES="$(python tests/e2e_pollers.py --overrides)" flowwolf:latest
python tests/e2e_pollers.py http://localhost:5679
# webhook-registering triggers against fake services
docker run -d --rm --name fw-e2e -p 5679:5678 -e FLOWWOLF_API_BASE_OVERRIDES="$(python tests/e2e_hooktriggers.py --overrides)" flowwolf:latest
python tests/e2e_hooktriggers.py http://localhost:5679
# request contracts of declarative integrations against echo mocks (fixed hosts redirected)
docker run -d --rm --name fw-e2e -p 5679:5678 -e FLOWWOLF_API_BASE_OVERRIDES="$(python tests/e2e_contracts.py --overrides)" flowwolf:latest
python tests/e2e_contracts.py http://localhost:5679
# parked executions across a container restart (container needs a named volume, ~80 s)
python tests/e2e_persist.py http://localhost:5679 fw-e2e
# Git sync against a real Gitea + share roles: two fresh instances, Gitea API URL as seen from containers/host, token
# PostgreSQL: main + two workers without a shared volume, then the core suite on PostgreSQL
sh tests/pg_cluster.sh
# queue mode: main (EXECUTIONS_MODE=queue) + two workers (hostnames w1/w2, FLOWWOLF_WORKER_CONCURRENCY=3) on one volume
python tests/e2e_queue_workers.py http://localhost:5679
# Git over SSH: Gitea with its SSH port on 3322, two fresh instances
python tests/e2e_git_ssh.py http://localhost:5679 http://localhost:5680 ssh://git@host.docker.internal:3322/flow/automations.git http://localhost:3300/api/v1 <token>
python tests/e2e_git_roles.py http://localhost:5679 http://localhost:5680 http://host.docker.internal:3300/api/v1 http://localhost:3300/api/v1 <token>
```

Layout: `src/engine.rs` (execution), `src/js.rs` + `src/prelude.js` (sandbox), `src/nodes/` (node logic and
`nodes.json` descriptions), `src/api.rs` (HTTP), `src/triggers.rs` (schedules/webhooks), `web/` (website and editor,
embedded into the binary).
