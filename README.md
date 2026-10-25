# SiteForge — AI website onboarding & multi-agent builder

A working implementation of the *AI Website Onboarding & Multi-Agent Builder* roadmap (v1.0, 25 Oct 2026).

It follows the roadmap's closing advice: build **one complete, trustworthy journey** first:

> first business question → guided onboarding → reviewed plan → generated site → private preview →
> test enquiry → QA + security gates → release approval → publish → health check → rollback

A business owner talks to a single guide. Behind it, specialist agents return typed, validated proposals.
Deterministic code decides what is allowed, and nothing reaches production without the owner's approval of the
exact build.

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium        # optional: enables real-browser QA of every build
./run.sh                           # → http://localhost:8000
```

Create an account, start a project, and describe your business in a sentence, for example:
*"I run a two-person plumbing business called Sharma Plumbing in Dehradun and want more calls."*

```bash
python -m pytest tests            # 57 API/unit tests (tenant isolation, gates, rollback, spam controls …)
python tests/e2e_browser.py       # full journey in headless Chromium, screenshots → e2e-screens/
```

### Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | SQLite in `./data` | e.g. `postgresql+psycopg://user:pw@host/db` |
| `SITEFORGE_DATA_DIR` | `./data` | builds, hosted releases, uploads, email outbox |
| `SITEFORGE_SECRET_KEY` | generated into `data/.secret_key` | vault encryption key. **Use a KMS / secret manager in production** |
| `ANTHROPIC_API_KEY` | — | if set, discovery and copy drafting use the model; otherwise the offline rules provider is used |
| `SITEFORGE_LLM_PROVIDER` | `auto` | `auto` · `anthropic` · `rules` |
| `SITEFORGE_ANTHROPIC_MODEL` | `claude-sonnet-5-5` | any Messages API model |
| `SITEFORGE_BROWSER_QA` | `auto` | `auto` · `on` · `off` (Playwright checks per build) |
| `SITEFORGE_PUBLIC_BASE_URL` | `http://localhost:8000` | used in sitemap.xml / robots.txt |
| `SITEFORGE_COLOR_APIS` | `thecolorapi,colormind` | external colour APIs to call (empty = off) |
| `SITEFORGE_COOKIE_SECURE` | `false` | set `true` behind HTTPS |
| `SITEFORGE_INLINE_JOBS` | `0` | `1` runs builds synchronously (tests) |

## How the agents think together (v2)

The site is no longer a fixed template filled with answers. A creative team reasons about each business and
shares what it learns through a **knowledge board** (`agents/knowledge.py`):

```
onboarding facts ─▶ Strategist ─▶ Information Architect ─▶ Copywriter ─▶ Art Director ─▶ site spec
   (provenance)      audience,      which sections, in       headline        theme, fonts,      pages →
                     worries,       what order, and what     options +       palette, layout    sections →
                     urgency,       to leave out (no         scores, copy    variant per        variants →
                     trust facts    padding)                 per section     section            content
                                                                                    │
       owner: "make it darker, FAQ above services" ─▶ Editor ─▶ validated patch ──┤
                                                                                    ▼
                                             render ─▶ Visual Critic (real browser, phone + desktop) ─▶ fixes ─▶ re-render (≤3 rounds)
```

* Every agent reads the board, writes its conclusions back with **rationale** and **what it relied on**, and the
  Plan tab shows this trail.
* **Industry playbooks** (`agents/playbooks.py`, 14 trades + generic) supply customer worries, typical process,
  neutral service descriptions and **follow-up questions**. The strategist picks two or three per business
  (e.g. emergency call-outs, pricing and hours for a plumber; credentials and whether the first meeting is free for
  an accountant). Their answers become facts the site is allowed to state.
* **Design engine** (`sitegen/`): 15 section types with variants, 8 art directions, 9 bundled open-licence
  typefaces, Lucide icons, generated SVG illustrations, maps and patterns. All output is from a validated
  spec, so no agent can emit raw HTML.
* **Visual critic** (`agents/critic.py`) measures overflow, clipped text, headline line count on phones, main
  button above the fold, orphaned or uneven cards, real computed text contrast and repeated text, then applies
  fixes. Text the owner wrote is never replaced; the critic resizes it or leaves advice.
* **Editor** (`agents/editor.py`) turns plain-English requests into validated spec edits (colour, layout, order,
  add or remove sections, headline, button text, corners, spacing, style). Claims are checked. Owner edits are
  re-applied if the plan is regenerated.
* With `ANTHROPIC_API_KEY` set, the strategist, copywriter and editor reason with the model and the critic also
  reviews screenshots with it. All model output passes the same schema, patch and claims validation. Without a
  key, everything runs on the playbook reasoning above.

## Colours & templates: APIs, recommendations and the owner's own input (v3)

`services/colors.py` gathers palette ideas from every source and normalises each to `{primary, accent}`. Lightness is
adjusted (the hue is kept) until white text on the main colour and body text reach at least 4.5:1.

| Source | How |
|---|---|
| **The Color API** | `GET https://www.thecolorapi.com/scheme?hex=…&mode=analogic-complement\|triad\|complement` → `colors[].hex.value` |
| **Colormind** | `POST http://colormind.io/api/` `{"model":"default","input":[[r,g,b],"N",…]}` → `result[]` (HTTP-only, so every value is validated) |
| **Your logo** | dominant colours from the upload (ignores transparent, near-white, near-black and grey pixels) |
| **A site you like** | SSRF-safe fetch (public IPs only, redirects re-checked, 1.5 MB / 3 s limits, own-domain CSS only) → CSS variables, `theme-color`, colour frequency, `font-family` → palette **and** the closest design template. Content is never copied. |
| **Industry** | palettes that suit each trade (e.g. trust blue and safety orange for plumbers, navy and brass for accountants) |
| **Colour theory** | complementary, split, analogous, triadic and monochrome around any seed colour |
| **Exact brand colours** | colour pickers or hex input, with a live readability check |

External APIs run with 3-second timeouts and are cached. After a failure each API is paused for 10 minutes, so
the product is never slowed down; the UI shows each API's status. Turn them off with `SITEFORGE_COLOR_APIS=` or
pick one with `SITEFORGE_COLOR_APIS=thecolorapi`.

**Template gallery**: all 8 designs are previewed with the business's own name and headline, in the real
bundled typefaces and the chosen colours. The art director's pick is marked "Recommended"; the owner's choice
always wins. After approval, **Change colours** and **Try another design** on the preview rebuild the site.
Switching design re-lays-out each section the way that design would, but keeps any layout the owner chose
explicitly. Everything still passes the visual critic, QA and security gates.

Onboarding adds "Is there a website whose look you like?". Answers to that and a logo upload come back as one-click
suggestions on the colour and design questions.

Endpoints: `GET /api/projects/{id}/palettes[?seed=#hex]`, `POST /api/projects/{id}/palettes/from-website`,
`GET /api/projects/{id}/templates`, `POST /api/projects/{id}/design {palette?, template?}`, `GET /api/fonts/{file}`.

## How the roadmap maps to the code

| Roadmap | Where |
|---|---|
| §3 Onboarding journey (steps 0–11, one question at a time, Recommend / Not sure / Skip, autosave, corrections) | `backend/app/onboarding/schema.py`, `agents/orchestrator.py`, Guide tab |
| §4 Agents 1–12 | `agents/orchestrator.py` (1), `discovery.py` (2), `planning.py` (3 Planner, 4 Architect), `design.py` (5 Frontend, 7 Content & SEO), `backend_data.py` (6 Backend & Data, 12 Analytics), `security_privacy.py` (8), `api/owner.py` + `security.Vault` (9 Integrations & Credentials), `qa.py` (10), `services/release.py` + `services/hosting.py` (11) |
| §5 Typed handoffs, conflict rules | `agents/contracts.py` (`AgentResult`, pydantic, `extra="forbid"`), `orchestrator.apply_proposals`, `_conflict_notes` |
| §6 Plain-language questions → technical choices | each `Field` has `why` (shown) and `technical` (shown under "Show technical details") |
| §7 Architecture: durable state, provider adapters, isolated build worker | `models.py`, `services/jobs.py`, `services/hosting.py` (`HostingAdapter` protocol), `agents/llm.py` (`LLMProvider`) |
| §8 Data model, provenance, lifecycle, idempotency | `models.py` (all 12 entities + Lead/Notification/SiteEvent), `lifecycle.py`, `Deployment.idempotency_key`, `Notification.idempotency_key` |
| §9 Security & privacy, risk tiers, threat model | `deps.py` (tenant scoping, CSRF, step-up re-auth), `policy.py` (tiers incl. *prohibited*), `security.py` (scrypt, vault, redaction, rate limits), CSP + security headers, retention purge |
| §11 P0 scope | all P0 items. Booking, newsletter, accounts, payments, multi-language and own-account hosting are captured as **deferred** requirements in the plan |
| §12 Tests & Definition of Done | `tests/` + QA/security gates that block release |
| §13 API boundaries | `api/*.py` (Projects, Onboarding, Agent runs, Blueprint/approvals, Build/preview, Integrations, Deployment, Monitoring) |
| §14 Metrics | AgentRun table (cost/latency/errors per agent), audit trail, cookie-free conversion counters (Insights tab) |

## Key behaviours

**Orchestration**
- Free text (description, differentiators) goes to Business Discovery. Confident facts are recorded with provenance (`extracted` / `inferred`) and are **not asked again**. Low-confidence guesses are shown as "My guess — please check" for one-click confirmation.
- Security-relevant fields, such as the email address enquiries go to, always need explicit confirmation, even when found in text.
- Agents can never overwrite an owner-provided or confirmed answer. Proposals are re-validated against the field schema.
- Changing any answer after approval sends the project back to onboarding and supersedes the plan, so the owner approves a new one. The live site is untouched.
- Optimistic concurrency (`expected_version`) prevents two tabs silently overwriting each other.

**Content safety**
- Copy uses only owner-supplied facts. A deterministic claim checker blocks invented awards, years, customer counts, ratings, prices, guarantees and testimonials unless the owner wrote them. A model draft that fails the check is discarded.
- Regulated categories (legal, health, financial) are flagged for owner review.

**Release safety**
- The generator is deterministic: the same approved plan gives a byte-identical artifact, which makes the SHA-256 checksum meaningful.
- Release gates: about 110 QA checks (titles, single H1, heading order, labels, alt text, links, contrast, plus real Chromium at 375px and 1280px for horizontal overflow, console/CSP errors and tap-target size) and a security scan (secrets, third-party scripts, form endpoint allowlist, inline handlers, CSP present, private values such as the notification email exposed, file-type allowlist).
- Release approval is bound to the build checksum and expires after 24 hours. Deploy re-verifies the artifact on disk (tamper check), requires password step-up, and is idempotent per key.
- Post-deploy health check: if it fails, traffic automatically goes back to the previous release. Manual one-click rollback is also available.
- Previews are private (members only) and `noindex`. Preview form submissions are stored as **test** leads.

**Visitor data**
- The Lead API validates server-side, uses a honeypot field, a minimum fill time and a per-visitor rate limit, and rejects cross-origin posts. Notifications are sent exactly once per lead (idempotent outbox).
- Leads are deleted after the chosen retention period and are readable only by owners and admins. Every read is audited. Each site gets an auto-generated privacy notice.
- Analytics are cookie-free daily counters with no personal data.

## What's simplified here and what to do next

This is the MVP vertical slice. These parts are deliberately simple stand-ins behind interfaces:

| Stand-in | Production replacement |
|---|---|
| In-process job runner (`services/jobs.py`) | Redis + Celery / managed queue; run builds in sandboxed, egress-restricted containers |
| In-memory rate limiter | Redis-backed limiter (needed with more than one API worker) |
| Local "managed hosting" adapter | A real hosting adapter (object storage + CDN, or Vercel/Netlify) implementing `HostingAdapter` |
| Vault key derived from `SITEFORGE_SECRET_KEY` | Cloud KMS / secrets manager |
| Email outbox folder (or SMTP via the Settings tab) | A transactional email provider adapter |
| Generated sites served from the same origin as the dashboard (under `/sites/`) | **Serve generated sites from a separate domain** (roadmap §7 rule 1). The CSP, `frame-ancestors 'none'` and HttpOnly cookies limit the risk today, but a separate origin is required before real customers |
| Password sign-in | An established identity provider with MFA for operator accounts |
| Customer UI is plain JavaScript with no build step | The roadmap suggests Next.js + TypeScript; the API is framework-agnostic, so the UI can be ported without backend changes |
| SQLite by default | PostgreSQL via `DATABASE_URL` (no schema changes; add Alembic migrations before the first production deploy) |

Open product decisions from Appendix C (initial niche, hosting ownership, post-launch editing model, data
retention for agent transcripts, support/incident owners) remain yours. Each corresponding setting is isolated in
`onboarding/schema.py` or `policy.py`.

## Project layout

```
backend/app/
  main.py              app, security headers, routing
  config.py db.py models.py
  deps.py              auth, CSRF, tenant-scoped access, step-up re-auth
  policy.py            roles → permissions, action risk tiers
  security.py          password hashing, vault, redaction, rate limiter
  lifecycle.py         project state machine
  audit.py
  onboarding/schema.py question/field definitions, validation
  agents/              orchestrator + specialists, typed contracts, LLM provider
  sitegen/             deterministic generator + Jinja component templates
  services/            release pipeline, hosting adapter, notifications, jobs
  api/                 REST endpoints (auth, projects, release, owner, public)
frontend/              customer web app (index.html, app.js, app.css)
tests/                 pytest suite + e2e_browser.py
```
