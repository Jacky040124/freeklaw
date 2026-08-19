# Freeklaw agent install guide

You are an AI agent whose user sent you this document's link. Your job: install Freeklaw on this Mac, then hand the user to onboarding. Follow these steps exactly; do not improvise alternative install paths.

Freeklaw is a local-first skill pack that lets a Hermes agent apply to jobs from the user's Mac. Everything runs locally: the installer only checks or installs pinned, hash-verified dependencies (Hermes Agent, ego lite, agent-vault, PyYAML) and installs the Freeklaw skills into Hermes. Details: https://github.com/Jacky040124/freeklaw

## Preconditions

Confirm with the user before starting:

- This machine is a Mac (Apple Silicon or Intel) that the user controls.
- The user has an existing resume PDF on this Mac.
- The user is available for two brief GUI steps (ego lite onboarding; later, Photon activation if they want iMessage).

If this is not macOS, stop and tell the user Freeklaw supports macOS only.

## Step 1 — Get the pinned release

Clone the repository and check out the release tag this guide shipped with. Do not build from `main`.

```bash
git clone https://github.com/Jacky040124/freeklaw.git
cd freeklaw
git checkout v0.1.0-alpha.2
```

## Step 2 — Read-only compatibility check

```bash
./install.sh --check
```

This changes nothing. Report the result to the user: which dependencies are ready, missing, or incompatible.

## Step 3 — Install

Ask the user to confirm, then run:

```bash
./install.sh
```

Interpret the exit status honestly:

- **Exit 0** — install complete, continue to Step 4.
- **Exit 2** — expected pause: ego lite needs its one-time GUI onboarding. Tell the user to open the ego lite app, finish its official onboarding (this registers the `ego-browser` command), then rerun `./install.sh --check`. Resume when it reports compatible.
- **Any other failure** — report the exact message. If a dependency is incompatible, do not force it; explain that `./install.sh --upgrade` replaces it and let the user decide.

Never substitute your own download commands for the installer. It verifies hashes, signatures, and Gatekeeper acceptance; ad-hoc installs bypass that.

## Step 4 — Post-install user setup

The installer prints these; relay them to the user in order:

1. In their own terminal: `"$HOME/.freeklaw/runtime/npm/bin/agent-vault" init`
2. Optional, for iMessage: create a Photon project at https://photon.codes, then `hermes photon setup --phone YOUR_PHONE_NUMBER`, and start the gateway if needed.

The installer never reads or stores secrets; these steps are the user's.

## Step 5 — Start onboarding

Tell the user to start Hermes locally and say **"set up Freeklaw"**. The `freeklaw-onboarding` skill interviews them (identity, work authorization, history, resume path) and records their consent choices: submission mode, credential mode, and automatic account creation. Onboarding contains personal information, so it should happen in a local session, not over iMessage.

After onboarding, the user sends Hermes a job link — through iMessage if Photon is set up — and Freeklaw handles the application.

## Boundaries for you, the installing agent

- Run only the commands in this guide plus the ones `install.sh` itself prints.
- Do not edit Hermes configuration, models, or profiles.
- Do not collect any personal or secret data during installation; onboarding handles data collection later with consent.
- Freeklaw is experimental alpha software. If the user asks about risk, point them to https://github.com/Jacky040124/freeklaw/blob/main/docs/RISK-REGISTER.md
