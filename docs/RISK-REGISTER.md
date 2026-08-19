# freeklaw — Assumption Verification Risk Register (2026-08-18)

8 Opus researchers, ~86 evidence-backed verdicts against the live Hermes source, live web/API probes, and live tool tests. **Verdict: the plan as written does not survive contact. It needs a scope change, not a patch.**

> **Alpha disposition (2026-08-19):** implementation has started as two Hermes skills plus a thin dependency/setup script. The alpha accepts arbitrary user-supplied sites only on a best-effort basis, uses an existing PDF unchanged, defaults to approve-each, defers discovery and resume tailoring, and targets an always-on Mac mini for acceptance. Auto-submit and automated credential use remain optional experimental features; they are not presented as hardened against malicious page content. This register remains the evidence baseline, not a claim that every risk below has been removed.

## The five findings that change the plan

**1. Nobody is paying the inference bill — and it's the whole business model.**
A pure-interactive apply = 25–60 tool calls, each resending the transcript; one Greenhouse snapshot alone is ~5–7.5k tokens. One application ≈ 1.2M input tokens ≈ **$2.16 on Sonnet**. At Kleo's own default of 10 apps/day → **$130–$650/mo per user**. "Free" is only true of our code; the user's model bill is larger than Kleo's subscription. *This is the single biggest unknown unknown.*

**2. The laptop premise is backwards.** Verification happened on an always-on AC-powered Mac mini. The target user has a MacBook that sleeps on lid-close (~1–2 min idle on battery). launchd `KeepAlive` restarts a *crashed* process; it does nothing about system sleep. "Text a link while you're out" is exactly the scenario that cannot work. Also: `hermes gateway install` creates a **LaunchAgent** → starts at GUI *login*, not boot; after a FileVault reboot the agent is dead until a human logs in.

**3. Resume upload — the one non-optional step in a job application — is impossible in Hermes.** Its native browser toolset has **no file-upload tool** (`toolsets.py:171-181`); only raw `browser_cdp` hand-rolling. It works *only* via ego-browser's `uploadFile()` (verified live against a real Greenhouse form). So ego-lite is a **hard dependency** — and it's a closed-source proprietary Chromium fork (MIT covers only the skill markdown), macOS-only, installed via DMG + Gatekeeper + a **mandatory GUI onboarding a CLI cannot script**. Our "MIT, zero-infra" story depends on one vendor's free CDN binary.

**4. The human-in-the-loop UX doesn't reach a remote human.** ego's handoff hands control of a *local browser window* to someone sitting at that Mac. Captchas, 2FA on employer-account creation, and Cloudflare walls therefore **cannot be resolved from a phone** — the agent can only text "come to your Mac." Worse, account-confirmation codes arrive in the user's *email*, so they'd alt-tab and paste per employer, ~10×/day. (Kleo hit this and built IMAP ingestion.) Essay questions are the one thing that genuinely works remotely.

**5. Long turns break the runtime at defaults.** Per-turn budget is **90 API calls** — a multi-page application plus resume tailoring blows past it. `busy_input_mode` defaults to `interrupt`, so a user texting a second link mid-application **aborts the running application**. The keep-alive heartbeat has no message-edit support on iMessage → **a new text every 3 minutes** (~15 per application) which reads as spam and risks flagging the line. And `/background` — the obvious fix — **can't ask questions at all** (no clarify callback).

## Everything else, briefly

**Works:** Photon free tier is real (signup no waitlist/card, shared number assigned synchronously, 5 req/s, unlimited daily msgs); only 2 secrets, no public ingress; Hermes skill format (dir + SKILL.md frontmatter) is fine; `gateway install` is no-root and prompt-free on macOS; MIT/legal exposure is Selenium-like since the user is the operator.

**Broken/caveated in ways we must code around:**
- `hermes photon setup` is **not non-interactively drivable** — it returns `""` for prompts when stdin isn't a TTY, prints "✓ setup complete", and the gateway then **default-denies the user's own texts**. Must pass `--phone` explicitly.
- `photon setup` **rotates the project secret on every run**, including the "reusing existing project" path — and exits 1 *after* rotating, so a retrying installer rotates repeatedly and silently kills the running sidecar.
- **`pip install hermes-agent` ships no Photon sidecar** (excluded from package-data) → freeklaw must mandate the git-clone install.
- spectrum-ts is pinned at **3.1.0 vs upstream 12.7.0**; a patch applied by literal string match that hard-exits on mismatch.
- Inbound attachments work, but the adapter tells the model *"attachments arrive as metadata only"* → **the agent will refuse to read the resume the user just texted.**
- `gateway status` shows green for crash-loops and wedged processes (no heartbeat).
- Agent turns run **in-process** with the gateway: a browser-automation OOM kills the gateway and every in-flight application, no resume.
- The gateway plist **freezes PATH at install time** → install order matters (ego lite before gateway install, or `ego-browser: command not found` forever).
- **agent-vault**: `init` requires a TTY (use `set --stdin`); there is **no vault-run/env-injection** — the only consumption path materializes plaintext to disk; and it gives **zero protection against an agent with shell access**, which ours has by design. It's Apache-2.0 (not MIT) and has had no commits in 6 months while holding ATS passwords.
- Skill distribution via `hermes skills install <repo>` is **blocked by Hermes's own security guard** — a realistic apply skill tripped 3 CRITICAL findings and `--force` does not override.
- Users with iOS "Filter Unknown Senders" **won't be notified** of the agent's texts (random pool number), silently killing approve-each.
- Kleo uses **SendBlue, not Photon** — so Photon has no reason to object; that risk was backwards.
- **Name**: "FreeKlaw" is "KleoKlaw" minus a letter, same class, same users — textbook likelihood of confusion. GitHub handle `FreeKlaw` is already taken.

**Biggest security finding (nobody assigned it):** the ego agent inherits the user's **entire Chrome cookie jar** by design, and we point an improvising LLM at arbitrary job postings. A prompt-injected job description has a live authenticated path to the user's email/banking, plus shell access to dump the vault.

## Original recommended plan changes

These recommendations record the audit conclusion. The alpha adopts the cost disclosure, always-on positioning, preflight ownership, and security warnings. It deliberately retains the Freeklaw name and permits best-effort attempts beyond Greenhouse; neither choice should be mistaken for risk closure.

1. **Re-scope v1 from "applies to any site" → "assisted apply on 1–2 ATSes"** (Greenhouse first — verified drivable). A funded managed competitor ships exactly two and punts the rest; we can't beat that on a smaller budget with no upload primitive.
2. **State the inference cost in the README** and default to a cheap model (GLM/Qwen ≈ $0.03–0.43/app vs $2.16 Sonnet).
3. **Reposition from "while you're out" → "your Mac applies while you're at your desk."** Or require a desktop/always-on Mac and say so on line 1.
4. **Own preflight entirely**: awake-check, PATH/order enforcement (ego before gateway), `--phone` for photon setup, a rotation-safe setup wrapper, TTY-free vault usage, and a smoke test that proves an iMessage round-trip before declaring success.
5. **Pick a different name.**

## Alpha release evidence still required

1. **One real end-to-end application** via ego-browser, driven from iMessage in approve-each mode, measuring total tokens/cost when available, wall-clock time, and iteration count.
2. **Restart recovery test** on the always-on Mac mini: interrupt the gateway mid-application, inspect the persisted checkpoint and live browser state, and prove final submission is not replayed. Laptop lid-close behavior remains a documented unsupported operating condition rather than the alpha acceptance target.
3. **Fresh-machine install rehearsal** on a later clean macOS account or machine. The first alpha is accepted on the existing Mac mini, so it cannot close this distribution risk.
4. **Skill-guard test**: install both published skill documents through Hermes's normal scanner with no bypass.
5. **Prompt-injection probe**: use a controlled job page containing hostile instructions that request local data, credentials, unrelated navigation, changed consent, and shell execution.
