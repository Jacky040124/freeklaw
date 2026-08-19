---
name: freeklaw-onboarding
description: Set up or update a private Freeklaw application profile.
license: MIT
metadata:
  version: 0.1.0
  author: Freeklaw contributors
  platforms: [macos]
  hermes:
    tags: [Jobs, Applications, Onboarding]
    category: productivity
---

# Freeklaw onboarding

Create or update the user's local Freeklaw profile. Freeklaw is a skill running inside Hermes, not a separate agent or service.

Use this skill when the user asks to configure Freeklaw, change application details, choose approval or auto-submit mode, or save a reusable answer. Do not load it for an ordinary job link after onboarding is complete.

## Boundaries

- Prefer a local Hermes session for onboarding. If this skill was opened through iMessage or another gateway, explain that the interview contains personal information and offer to continue locally.
- Store profile data only in `~/.freeklaw/profile.yaml`. Never put credentials, API keys, passwords, page captures, or chat transcripts there.
- Never ask the user to paste a password or API key into chat. For a site credential, tell the user to run `"$HOME/.freeklaw/runtime/npm/bin/agent-vault" set <key>` themselves in a local terminal. If the user nevertheless chooses to send a password in chat, store it immediately with `"$HOME/.freeklaw/runtime/npm/bin/agent-vault" set <key> --stdin`, never repeat or reference its value, and remind them it remains in the transcript.
- Do not edit Hermes configuration, model selection, profiles, gateway settings, or conversation behavior.
- Do not start a job application from this skill. Finish onboarding, then let the ordinary `freeklaw` skill handle links.

## Preflight

Run these checks without changing the machine:

```bash
test -x "$HOME/.freeklaw/bin/freeklaw-state"
command -v hermes
command -v ego-browser
test -x "$HOME/.freeklaw/runtime/npm/bin/agent-vault"
```

If a check fails, stop and direct the user to rerun Freeklaw's tagged-release `./install.sh`. Do not improvise dependency installation from the skill.

## Interview

Ask only for missing or requested information. Keep the conversation natural rather than exposing a command protocol. Collect:

- legal name, preferred name, email, phone, and location;
- work authorization and sponsorship requirements;
- education and employment history;
- reusable, user-confirmed application answers;
- the absolute path to an existing resume PDF;
- submission mode: `approve_each` or `auto_submit`.
- credential use: `human_handoff`, `approve_each_fill`, or `auto_fill`.
- account creation: whether the agent may create applicant accounts automatically (`auto_create_accounts`, default false).

Never infer a factual answer. It is fine to leave an optional field absent.

`approve_each` is the submission default. Before accepting `auto_submit`, clearly explain that arbitrary job pages are untrusted and that Freeklaw's current local setup is experimental rather than hardened. Record its `experimental_warning_ack: true` only after the user explicitly accepts that risk.

`human_handoff` is the credential default: the user types or approves the login in the handed-off browser. `approve_each_fill` allows the vault bridge only after the user approves each specific fill. `auto_fill` allows the vault bridge without per-fill approval for logins on the employer's own application flow; it is the broadest and least supervised mode. Both non-default modes require a separate explicit acknowledgement that a shell-capable agent and browser processing an untrusted page do not provide a hardened credential sandbox. Never treat the submission acknowledgement as credential-use consent.

`auto_create_accounts: false` is the account-creation default: the agent asks before registering any applicant account. Setting it to `true` lets the agent register accounts with profile data without asking each time; the installed secret helper generates each new password directly into the vault, so the agent never sees it. This choice also requires the credential-use acknowledgement. Explain the trade-off before recording `true`.

## Save and verify

Build a temporary YAML document with every required top-level field. Empty mappings and lists are allowed when the user has no value. Do not copy the example values literally:

```yaml
schema_version: 1
identity:
  legal_name: "User-provided name"
contact:
  email: "user-provided@example.com"
work_authorization: {}
education: []
experience: []
reusable_answers: {}
resume_pdf: "/absolute/path/to/existing-resume.pdf"
consent:
  mode: approve_each
  experimental_warning_ack: false
credential_use:
  mode: human_handoff
  experimental_warning_ack: false
  auto_create_accounts: false
```

Give the temporary file owner-only permissions, then validate and save it atomically through the installed helper. Use `--root` only in tests; normal use relies on `~/.freeklaw`.

```bash
chmod 600 /path/to/profile-candidate.yaml
"$HOME/.freeklaw/bin/freeklaw-state" profile-validate /path/to/profile-candidate.yaml
"$HOME/.freeklaw/bin/freeklaw-state" profile-save /path/to/profile-candidate.yaml
"$HOME/.freeklaw/bin/freeklaw-state" profile-show
```

Delete only the exact temporary candidate created for this interview. Report a short summary of completed sections, the resume filename, the submission mode, and the credential-use mode; do not echo the full profile into chat.

When updating a single value or saving a reusable answer, first read the existing profile with `profile-show`, change only the user-requested field, and save it atomically with `profile-save`. Do not erase unrelated fields.
