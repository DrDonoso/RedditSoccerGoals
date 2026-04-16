# Work Routing

How to decide who handles what.

## Routing Table

| Work Type | Route To | Examples |
|-----------|----------|----------|
| Architecture & design | Leela | System design, API contracts, integration strategy |
| Background process | Fry | Event triggers, scheduling, process lifecycle |
| Reddit integration | Fry | Reddit API, media retrieval, title matching |
| Match data integration | Fry | Soccer match events, goal detection, data sources |
| Code review | Leela | Review PRs, check quality, suggest improvements |
| Testing | Hermes | Write tests, find edge cases, mock external APIs |
| Scope & priorities | Leela | What to build next, trade-offs, decisions |
| Docker & builds | Bender | Dockerfile, docker-compose, image builds, CI/CD |
| DevOps & deploy | Bender | Container config, pipelines, infrastructure |
| Security audit | Nibbler | Secret scanning, vulnerability checks, dependency safety |
| Security review | Nibbler | Code review for secrets, OWASP issues, Docker security |
| Session logging | Scribe | Automatic — never needs routing |

## Issue Routing

| Label | Action | Who |
|-------|--------|-----|
| `squad` | Triage: analyze issue, assign `squad:{member}` label | Leela |
| `squad:leela` | Architecture/design issues | Leela |
| `squad:fry` | Implementation, API integration, background process | Fry |
| `squad:hermes` | Testing, quality, edge cases | Hermes |
| `squad:bender` | Docker, CI/CD, builds, deployment | Bender |
| `squad:nibbler` | Security audits, secret scanning, vulnerabilities | Nibbler |

### How Issue Assignment Works

1. When a GitHub issue gets the `squad` label, the **Lead** triages it — analyzing content, assigning the right `squad:{member}` label, and commenting with triage notes.
2. When a `squad:{member}` label is applied, that member picks up the issue in their next session.
3. Members can reassign by removing their label and adding another member's label.
4. The `squad` label is the "inbox" — untriaged issues waiting for Lead review.

## Rules

1. **Eager by default** — spawn all agents who could usefully start work, including anticipatory downstream work.
2. **Scribe always runs** after substantial work, always as `mode: "background"`. Never blocks.
3. **Quick facts → coordinator answers directly.** Don't spawn an agent for "what port does the server run on?"
4. **When two agents could handle it**, pick the one whose domain is the primary concern.
5. **"Team, ..." → fan-out.** Spawn all relevant agents in parallel as `mode: "background"`.
6. **Anticipate downstream work.** If a feature is being built, spawn the tester to write test cases from requirements simultaneously.
7. **Issue-labeled work** — when a `squad:{member}` label is applied to an issue, route to that member. The Lead handles all `squad` (base label) triage.
