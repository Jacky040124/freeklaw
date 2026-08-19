# freeklaw

**Text your Hermes agent a job link. It applies from your Mac.**

Freeklaw is an open-source, local-first skill pack for job applications. It does not run a hosted service or replace your agent: your existing Hermes installation does the reasoning, Photon carries iMessages, and ego lite drives the browser.

> **Status: experimental alpha.** Freeklaw accepts links from any job site on a best-effort basis. Captchas, 2FA, unusual controls, site policy, or unsafe page behavior can stop a run. Auto-submit and automated credential use are not hardened against a malicious page; read the [risk register](docs/RISK-REGISTER.md) before enabling them.

## What is included

- `freeklaw-onboarding`: interviews you locally and writes a private `~/.freeklaw/profile.yaml`.
- `freeklaw`: uses the profile, your existing resume PDF, and ego lite to complete a job application.
- `install.sh`: checks or installs the tested dependencies, guides unavoidable GUI setup, and installs both skills through Hermes's scanner.
- Small local helpers for profile validation, safe checkpoints, minimal history, duplicate detection, and credential-to-browser filling without printing the secret.

Freeklaw deliberately does **not** include its own agent runtime, model configuration, message queue, command language, job discovery service, or resume generator.

## Requirements

- macOS on Apple Silicon or Intel
- A model/provider already usable by Hermes
- A Photon account and a phone that can activate its assigned iMessage line
- Brief access to the Mac's GUI for ego lite onboarding, login/captcha handoffs, and approval when needed
- An existing PDF resume

The installer uses these upstream components:

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [Photon](https://photon.codes) through Hermes's built-in adapter
- [ego lite](https://lite.ego.app) as the only browser path
- [agent-vault](https://github.com/botiverse/agent-vault) for local credential references

## Install

Download a tagged Freeklaw release, inspect it, then run:

```bash
./install.sh --check
./install.sh
```

`--check` is read-only. A normal install reuses compatible dependencies and installs only missing ones. If an existing dependency is outside the tested compatibility set, the installer stops instead of changing it. Review the message and rerun with `--upgrade` only if you want Freeklaw to replace or update that dependency.

The installer never chooses a Hermes model, creates a Hermes profile, or edits Hermes conversation settings. It uses the existing active Hermes profile and official setup flows.

Ego lite requires a one-time GUI onboarding step. Photon setup requires browser authorization and an activation text from your phone. The installer prints the exact next steps at these boundaries; rerun `--check` after completing them.

## Use

Start Hermes locally and ask it to set up Freeklaw. The onboarding skill collects application facts, validates an absolute resume PDF path, and sets submission consent:

- `approve_each` is the default and pauses immediately before final submission.
- `auto_submit` is opt-in and requires explicit acknowledgement of the experimental security risk.

Credential automation has separate consent. The default is a browser handoff so you type or approve the login yourself. The optional `approve_each_fill` mode requires its own warning acknowledgement and your approval before every vault-backed fill.

After onboarding, send Hermes a job link through its normal conversation surface. With Photon configured, that surface can be iMessage. Talk to Hermes normally if you want status, cancellation, or a settings change; Freeklaw does not add special commands.

The agent never invents factual answers. If the profile does not contain an answer, it asks you and can save the answer only after you confirm.

## Local data

Freeklaw keeps owner-only files under `~/.freeklaw/`:

- the profile;
- at most one active application checkpoint;
- minimal outcome history used for status and duplicate warnings.

History excludes credentials, page HTML, screenshots, and full conversations. Employer passwords are not stored in the profile. New secrets are entered by you through agent-vault's local terminal flow.

## Honest constraints

- **Inference is not free.** Freeklaw's code is free, but your Hermes model/provider may charge for long browser sessions.
- **The Mac must be available.** A sleeping or logged-out Mac cannot run the agent or browser.
- **Any-site support is best effort.** Freeklaw reports a blocker instead of claiming universal completion.
- **Some steps are local.** Captcha, 2FA, browser login approval, and some native dialogs require control of the Mac.
- **Ego lite is a hard dependency.** It is free but closed-source, and its current download endpoint is not versioned.
- **This is experimental security software.** A skill prompt and agent-vault are not an operating-system sandbox around a shell-capable agent processing untrusted pages.
- **You are the operator.** Employer and job-board terms remain yours to follow. Freeklaw never attempts captcha solving or anti-bot evasion.

## Development

Run the local checks with:

```bash
uv run --with pytest --with pyyaml pytest -q
sh tests/test_install.sh
```

Before publishing a tag, validate both `SKILL.md` files with an Agent Skills validator and run Hermes `skills inspect` against the immutable tagged URLs.

See the [implementation plan](docs/IMPLEMENTATION-PLAN.md) for the alpha boundary and the [risk register](docs/RISK-REGISTER.md) for accepted risks and remaining release evidence.

## License

MIT
