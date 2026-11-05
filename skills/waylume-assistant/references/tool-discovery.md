# Tool discovery and usage

The workbench Settings → 工具状态 list is the starting index for integrations. For a running local service, read `GET http://127.0.0.1:8765/api/integrations/status`; use the configured `MCLOUD_DASHBOARD_PORT` if the service uses a different port. This is a read-only snapshot of the latest recorded provider status and checking instructions, not an inventory of every tool or a complete usage manual. If the service is unavailable, say so and use the current host’s available tool/skill discovery; do not edit the store directly.

Before using a relevant integration:

1. Check the list for the provider and its recorded status, verification method, check time and scope. Unknown, failed, stale or a missing entry does not establish that the tool is uninstalled. A passed check proves only its stated scope, not the current session’s tool availability or permission to a particular file, repository or account.
2. Discover the current host’s actual tools and relevant Skills. Read the selected Skill and current tool schema or CLI help before invoking it; use the exact currently exposed name and parameters. Do not guess names from prior sessions or treat a displayed status/preset prompt as usage documentation. Follow the host’s own tool and permission rules.
3. If the needed capability or access remains uncertain, use an appropriate read-only check when justified by the task and within authorization. The repository’s `scripts/check_integrations.py` runs checks and **writes** status results; it is not a read-only list command and may contact services. Do not run it automatically merely to read the list. Figma and other host-only tools may require checks within their host session.
4. If tools or permissions are unavailable, report the observed limitation and use only an authorized supported alternative. Never expose credentials or infer permission for external writes from an integration status.

The listed tools evolve. Use the live workbench and host discovery instead of maintaining a duplicated hard-coded catalog here.
