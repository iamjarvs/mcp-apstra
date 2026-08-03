# Security Policy

## Reporting a vulnerability

If you discover a security issue, please do not open a public issue with exploit
steps or sensitive details.

Report it privately to the repository owner and include:

- affected version or commit,
- impact summary,
- reproduction steps,
- suggested mitigation (if known).

## Secure configuration guidelines

- Never commit `config/instances.yaml`.
- Prefer environment variables for credentials in shared environments.
- Use strong, unique credentials for Apstra API access.
- Keep `MCP_VERBOSE=0` in normal operation to reduce credential exposure in logs.
- Rotate credentials immediately if they are exposed.

## Hardening checklist before publishing

- Confirm no live credentials exist in source, tests, or docs.
- Confirm no internal hostnames/IPs are exposed unintentionally.
- Confirm local-only files are gitignored (`config/instances.yaml`, `.env*`, logs).
- Review recent commits for accidental secrets before pushing.
