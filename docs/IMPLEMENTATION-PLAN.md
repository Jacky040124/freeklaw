# freeklaw — Hermes-first alpha plan (2026-08-19)

Freeklaw is a skill pack for an existing Hermes agent. It does not ship an agent runtime, choose a model, create a Hermes profile, or introduce its own conversation protocol.

## Alpha boundary

- macOS only; Apple Silicon and Intel are supported installation targets.
- Hermes is the only tested host in the first release. Skill content and helper interfaces avoid unnecessary Hermes internals so other hosts can be added later.
- Ego lite is the only browser path. There are no per-ATS scripts and no fallback to Hermes's browser.
- A user-supplied link from any site may be attempted, but completion is best effort and blockers are reported honestly.
- The user's existing PDF is uploaded unchanged. Resume tailoring is deferred.
- Job discovery, monitoring, cron, a custom work queue, custom commands, and a hosted service are out of scope.

## Components

### Thin installer

`install.sh` checks macOS and a versioned compatibility manifest, reuses compatible dependencies, installs missing ones from official sources, and refuses to replace incompatible existing tools unless the user explicitly selects `--upgrade`.

It guides the official Hermes/Photon and ego lite setup flows, installs agent-vault, creates a private Freeklaw helper environment, and installs both skills through Hermes's security scanner. Version, signature, and non-secret launcher checks cover installed dependencies; Photon authorization, the iMessage round trip, and browser login remain explicit acceptance steps. It never edits Hermes model, provider, profile, gateway, turn-budget, or message-input configuration directly.

### Onboarding skill

`freeklaw-onboarding` runs only for initial setup or requested profile changes. It collects identity/contact details, work authorization, education, experience, reusable confirmed answers, an absolute existing resume PDF path, and submission consent. It writes a validated owner-only `~/.freeklaw/profile.yaml` through a deterministic helper.

`approve_each` is the default. `auto_submit` requires an explicit acknowledgement that arbitrary page content plus a shell-capable local agent is not hardened against prompt injection. Credential use is separate: `human_handoff` is the default, while `approve_each_fill` requires its own risk acknowledgement and case-by-case approval.

### Application skill

`freeklaw` handles user-supplied job links through normal Hermes conversation. It validates the profile, checks the current checkpoint and likely duplicates, drives one ego lite task space, asks rather than inventing unknown factual answers, uploads the configured PDF, and submits according to consent.

Hermes owns ordinary message ordering and control requests. Freeklaw persists only one active checkpoint and minimal completed-run metadata so an interrupted run can be inspected before an explicitly approved resume.

### Deterministic helpers

Small local scripts validate and atomically save YAML, enforce file permissions, maintain one checkpoint, record redacted minimal history, detect likely duplicate URLs, and pass an agent-vault value into an ego lite password field without printing the value. They contain no application reasoning or model calls.

## Safety invariants

- Page content is untrusted data and cannot change consent or authorize local-file, credential, shell, or unrelated-account access.
- New secrets are entered by the user through agent-vault's local TTY flow, never through chat.
- Captcha, 2FA, native browser dialogs, and uncertain login/attestation steps require a user handoff.
- A final submit action is never replayed during recovery.
- History never stores credentials, page HTML, screenshots, or complete conversations.
- The product is explicitly experimental; instructions and redaction are not advertised as an OS-level sandbox.

## Alpha acceptance

1. Unit-test profile validation, permissions, atomic state transitions, redaction, duplicate normalization, stale-run recovery, and the credential bridge.
2. Test installer check/install/upgrade behavior with an isolated home and mocked upstream tools, including interrupted setup and incompatible versions.
3. Require both skills to pass structural validation and Hermes's install-time security scan.
4. Probe a controlled hostile job page that attempts to change consent, obtain local data, navigate to unrelated authenticated sites, or induce a shell command.
5. On Daniel's Mac mini default Hermes agent, complete Photon setup and agent-vault installation, then submit one genuine user-selected job in `approve_each` mode through iMessage and ego lite.
6. Record aggregate duration, iterations, and available model cost without retaining sensitive page contents.

The Mac mini acceptance run must not modify the existing `cfo` or `wonyoung` Hermes profiles. GUI handoffs are expected for Photon authorization, ego onboarding, login/captcha steps, and final review.
