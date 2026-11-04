# Host integration and validation

The installer links this repository's `skills/waylume-assistant/` directory into a host's discovery directory. File placement is not proof that a host can discover or load the Skill. Always report these states separately:

1. Host application/CLI detected.
2. Skill link exists and resolves to this checkout.
3. A fresh host session actually lists or invokes the Skill.

Do not change host security policies, sign-in state, API keys, MCP permissions, or global prompt files as an installation side effect.

## Known macOS targets

| Host | Candidate directory | Runtime check |
|---|---|---|
| Codex | `~/.codex/skills/waylume-assistant` | Open a new Codex session and confirm the skill is discoverable; invoke it and confirm its references resolve. |
| Gemini / Antigravity | `~/.gemini/config/skills/waylume-assistant` | Open a new Antigravity session and confirm skill discovery and reference loading. |
| DeepSeek Harness | `~/.agents/skills/waylume-assistant` (shared Agent Skills location) or `~/.dsh/skills/waylume-assistant` | Current upstream documentation lists both user-level roots. Confirm in a fresh DSH session; until then report file-installed/runtime-unverified. |

The installer uses the shared `~/.agents/skills` location. Upstream DSH filesystem Skill docs describe directory bundles with `SKILL.md` and the shared user root, but host-version behavior can vary; actual loading remains unverified until a fresh session discovers and loads the Skill. See [DSH filesystem Skill docs](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md). If the candidate directory differs on a future release, verify that host's current official documentation before changing the installer. Do not infer support merely from a directory existing or a successful symlink.

## New-machine checklist

- Install Git and Python 3; clone the repository into a stable location under the user's home directory.
- Run `python3 scripts/install_skill.py --check`; inspect detected hosts and conflicts.
- Install only the chosen hosts. Keep existing conflicting skill files untouched.
- Start a fresh session in each host and verify discovery. Record app/CLI version and observed result in a private setup note.
- Separately configure the Waylume workbench service/data directory and approved connectors. Skill installation does not create accounts, authenticate services, or migrate company data.
- Do not copy authentication material. Sign in using the host's own UI or documented secret store.
