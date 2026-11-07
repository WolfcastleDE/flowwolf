# Security policy

## Reporting a vulnerability

Please do **not** open a public issue. Report it privately through
[GitHub security advisories](https://github.com/WolfcastleDE/flowwolf/security/advisories/new).

Include what an attacker can do, the steps to reproduce and the affected version. We confirm receipt within a few
days, keep you posted on the fix and credit you in the release notes if you like.

## Supported versions

Security fixes go into the latest release. Please update to it before reporting.

## Hardening a deployment

- Run behind HTTPS and set `FLOWWOLF_URL` to the public `https://` address (session cookies are then `Secure`).
- Set a fixed `FLOWWOLF_ENCRYPTION_KEY` and keep it with your backups; credentials cannot be decrypted without it.
- Leave `FLOWWOLF_ENABLE_EXECUTE_COMMAND` off unless you need it; consider `FLOWWOLF_BLOCK_ENV_ACCESS=true`.
- Turn on two-factor authentication for the owner and admin accounts (Settings → Two-factor authentication).
