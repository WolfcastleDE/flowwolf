# Changelog

All notable changes are listed here. Versions follow [semantic versioning](https://semver.org).

## [Unreleased]

### Added

- Update notices: Flowwolf checks the GitHub releases once a day; the sidebar shows **Update x.y.z** and
  Settings → Updates lists the release notes. With `FLOWWOLF_UPDATE_WEBHOOK` set, owners and admins update with
  one click through the platform's deploy hook (Dokploy, Coolify, …).

### Changed

- License: from the next release on, Flowwolf is fair-code under the Sustainable Use License (as n8n). Using,
  modifying and self-hosting stay free; selling Flowwolf or offering it as a paid service is not allowed without
  a separate agreement. v0.1.0 and earlier remain available under MIT.
- Expressions and the Code node: `DateTime`, `$now` and `$today` use the instance time zone (`GENERIC_TIMEZONE`)
  like Luxon in n8n, with correct daylight saving time.

## [0.1.0] - 2026-11-08

The first public release.

### Workflows

- Runs n8n workflow JSON as it is: same nodes, parameters, expressions (`{{ }}`, `$json`, `$node`, `$vars`, Luxon),
  pinned data, error workflows, sub-workflows, waits and the n8n public API (`/api/v1`, `X-N8N-API-KEY`).
- Code node in JavaScript (QuickJS sandbox) and optionally Python (sandboxed RustPython build).
- Triggers: webhooks, forms, chat, schedules, polling triggers, 60+ service webhooks that register themselves,
  MQTT, Redis, RSS, local files, email (IMAP) and the MCP Server Trigger.
- 400+ node types: core nodes, AI agents and chains with sub-nodes (12 chat model providers, memory, tools,
  MCP client, vector stores incl. Qdrant, Pinecone, Supabase and PGVector) and REST integrations. Slack, Telegram,
  Discord, GitHub, Gmail, Google Drive, AWS S3, MongoDB and FTP/SFTP carry n8n's operation sets.

### Running it

- One binary with editor and website built in; Docker image of about 105 MB (amd64 and arm64), a few MB of RAM at idle.
- SQLite by default, PostgreSQL optional; queue mode with workers.
- Git source control (any server over SSH, or the GitHub / Gitea API).

### Users

- Owner, admin and member roles; sharing of workflows and credentials as editor or viewer.
- Invitations, password reset, deleting users with a transfer of their data, two-factor authentication with
  recovery codes, personal API keys.
