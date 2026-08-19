# freeklaw — Implementation Plan v2 (2026-08-18)

**freeklaw** — MIT, under Jacky's GitHub account. One-liner: *"Text your own agent a job link over iMessage. It applies. Everything runs on your machine. Free."*
Launch/ads mirror KleoKlaw's style (similar copy, per Jacky).

## Locked decisions
- User owns everything; we host/maintain **nothing**. Free, fully open source, MIT.
- **Hermes only** runtime. Browser work = **ego-lite interactive automation — NO per-ATS scripts** (nothing rots when sites change; agent improvises like 囵囵 does in #slp-application).
- Photon: user signs up themselves. **Free tier verified 2026-08-18** (photon.codes/pricing): iMessage unlimited daily messages, full DM API, 10 users, managed shared number, $0. Cold outreach is Business-only, but irrelevant — onboarding has the user text their own line first (activates the contact; this was the find-elon "Target not allowed" lesson).
- Discovery/monitoring **optional opt-in**; auto-apply only offered if monitoring is on. Default flow: user drops a job link → agent applies.
- Consent: onboarding choice, approve-each (default) vs auto-apply; changeable by text.
- Vault: github.com/botiverse/agent-vault.
- Profile: onboarding interview → `profile.yaml`.

## Verified Hermes built-ins (this Mac, 2026-08-18) — huge plan simplifiers
1. **`hermes gateway install`** → native launchd/systemd background service (start/stop/restart/status). **We ship zero keep-alive code.**
2. **Built-in Photon iMessage adapter** — `plugins/platforms/photon/` (adapter.py + auth.py + Node spectrum-ts sidecar): inbound gRPC stream → gateway, outbound text/typing/attachments, supervised sidecar, own `hermes photon setup`. **Custom photon-bridge DELETED from plan.**
3. **`hermes cron`** — scheduler for optional discovery.

## What the repo actually contains (thin!)
1. **Onboarding CLI** (`npx freeklaw init` or similar):
   - Preflight: macOS, Hermes installed+authed, machine-stays-awake guidance.
   - Photon: open signup → user creates free project → `hermes photon setup` → **user texts their own line once** (activation + E2E proof; wizard waits for the inbound).
   - Interview → `profile.yaml`: identity/contact/links, work auth, education, experience, base resume path, target roles/locations, caps, blocklist.
   - Consent step: approve-each (default) / auto-apply (only if monitoring enabled).
   - agent-vault init.
   - Install skill pack → `hermes gateway install` → agent texts "👋 alive".
2. **Hermes skill pack**:
   - `apply` (core): job URL → ego-lite interactive browser automation → create employer accounts as needed (creds → vault) → tailor + version resume → text user for essay questions/verification/captcha (human-in-the-loop over iMessage) → submit per consent → log → text ✅.
   - `status`, `settings` (change caps/consent/locations by text).
   - `discover` (optional, on hermes cron): scan configurable sources (public GitHub internship lists etc.), dedupe, text shortlist.

## Milestones
- **M0 (1–2d)**: repo + CLI onboarding E2E → profile.yaml + vault + `hermes photon setup` + gateway install → "text me and I reply" demo. (Fresh Photon signup during build doubles as final free-tier validation.)
- **M1 (2–4d)**: `apply` skill end-to-end on 2–3 real postings via ego-lite, human-in-the-loop questions, approve-each. Launch-video moment.
- **M2 (2–3d)**: employer-account creation + vault flows, resume versioning polish, `freeklaw doctor`.
- **M3**: optional discover skill, README (honest gotchas: Mac must stay awake; captcha = we text you, never bypass), demo video, **launch on X mirroring Kleo's copy**.

## Open items
- Exact repo/package name spelling (freeklaw), npm availability.
- Jacky's go for M0.
