# Security policy

This repository is a local simulation framework and a static showcase site.
It does not run a public service, but it does handle model-provider API keys
and publishes run transcripts.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub Security
Advisories on this repository. Do not open a public issue for a report that
includes a live credential, a private endpoint, or an exploitable detail.

Include the affected file or command, the impact, and a minimal reproduction
where possible. Please do not include real API keys in the report.

## Scope

In scope:

- Credential handling in `agent/src/simulation/models/` and `agent/src/simulation/env.py`
- The run-artifact exporter and published site data in `web/tools/` and `web/src/data/`
- The static site build and deployment configuration
- Accidental disclosure of local host, path, or performance detail in tracked files

Out of scope:

- Model output quality, game balance, or the fictional content of a simulation
- Vulnerabilities in third-party models, model servers, or hosting providers
- Denial of service against a local run that the operator started themselves

## Trust boundary

Configs are code. `llm.base_url`, `llm.api_key_env`, and `llm.extra_headers`
let a config choose where a key is sent, which environment variable is read,
and which headers are added. Only run configs you trust. The provider refuses
plaintext non-loopback endpoints, but it cannot distinguish a legitimate
HTTPS endpoint from an attacker's.

## Handling secrets

API keys belong in the gitignored `agent/.env` (mode 0600) or the shell
environment. Never put a key value in a tracked config; configs record only
the environment-variable name. See `agent/.env.example`.
