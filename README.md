# freeklaw

**Text your own agent a job link. It applies for you. Runs entirely on your Mac.**

Open-source, MIT, no hosted service, no subscription, no account with us — because there is no "us" to have an account with. You bring your own agent, your own iMessage line, and your own machine.

> **Status: pre-alpha, planning stage.** Nothing here installs yet. The design is done and every load-bearing assumption has been verified against real code and live APIs — including the ones that failed. Start with [`docs/RISK-REGISTER.md`](docs/RISK-REGISTER.md); it is the most useful file in the repo.

## How it will work

1. Run the freeklaw CLI on your Mac. It interviews you, writes a `profile.yaml`, and wires up your own free [Photon](https://photon.codes) iMessage line.
2. Text your agent a job link from your phone.
3. It opens the posting in a real browser, fills the application from your profile, tailors your resume, and texts you when it needs something only you can answer.
4. It submits — after your approval, or automatically if you turned that on during onboarding.

Inspired by [KleoKlaw](https://kleoklaw.com), which does this as a managed service. freeklaw is the version where you own everything.

## Honest constraints

These come from verification, not guesswork ([details](docs/RISK-REGISTER.md)):

- **You pay for inference.** Our code is free; the model calls are not. A single interactive application is roughly 1.2M input tokens — about **$2 on a frontier model, or a few cents on a cheap one**. Budget accordingly; freeklaw defaults to a cheap model.
- **Your Mac has to be awake.** A closed laptop lid stops everything. This works best on a desktop or an always-on machine.
- **macOS only**, and it depends on a third-party browser tool ([ego lite](https://lite.ego.app), free but closed-source) because the resume-upload step requires it.
- **Captchas and 2FA need you at the keyboard.** The agent will text you, but some walls can only be cleared in person.
- **You are the operator.** freeklaw runs on your machine with your accounts. Employer and job-board terms of service are yours to comply with. No captcha-solving or anti-bot evasion will ever ship here.

## Stack

Hermes agent runtime · Photon (iMessage, free tier) · ego lite (browser) · agent-vault (credentials) · `profile.yaml`

## Docs

- [`docs/RISK-REGISTER.md`](docs/RISK-REGISTER.md) — verified findings: what works, what's broken, what needs a spike. Read first.
- [`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md) — architecture and milestones (predates the register; being revised against it).

## Roadmap to v1

Five spikes stand between this and product code: one real end-to-end Greenhouse application (measuring true cost), a lid-close survival test, a fresh-machine install rehearsal, a skill-distribution test, and a prompt-injection probe. See the register.

## License

MIT
