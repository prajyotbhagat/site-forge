# Changes since the first version (v1)

This package contains only the files that are **new** or **modified** since v1, at the same paths. Unzip it over
your v1 folder (from the folder that contains `siteforge/`) and every file lands in the right place.

## ⚠ Do this first: remove the shipped secret key

The v1 **zip download** (not the GitHub repository, which is clean) accidentally included `siteforge/backend/data/.secret_key`, a throwaway key generated in my test environment.
If you run `./run.sh` from that folder, the app would use it to encrypt the secrets vault.

```bash
rm -rf siteforge/backend/data siteforge/backend/app/data
```

If you already ran v1 and stored real credentials (the SMTP password in Settings), delete the data folder as above,
start the app (it generates a fresh key) and enter those credentials again. `.gitignore` now excludes every
`data/` folder and `.secret_key`.

## Upgrading an existing v1 database

New tables (`knowledge_entries`, `site_specs`) are created automatically on start. One column is added to an
existing table. Run once on a v1 database:

```sql
ALTER TABLE builds ADD COLUMN spec_id VARCHAR(32);
```

Projects whose plan was approved under v1 have no site spec yet. Change any answer in the Guide and re-approve
the plan; the new creative team then generates one. A fresh install needs nothing.

Then run `pip install -r requirements.txt` (adds **Pillow**).

## New files (18, plus 31 font and licence files)

| File | Purpose |
|---|---|
| `backend/app/agents/knowledge.py` | Shared knowledge board: agents read it before acting and write decisions + rationale + what they relied on |
| `backend/app/agents/playbooks.py` | Industry knowledge base (14 trades + generic): customer worries, process, service descriptions, follow-up questions |
| `backend/app/agents/team.py` | Creative team: Strategist → Information Architect → Copywriter → Art Director; assembles the site spec |
| `backend/app/agents/critic.py` | Visual critic: renders in Chromium, measures layout problems, applies fixes, re-renders (≤3 rounds); optional vision review |
| `backend/app/agents/editor.py` | Turns plain-English change requests into validated spec edits |
| `backend/app/api/design_api.py` | Endpoints: palette recommendations, colours from a website, template gallery, colour/design switch, font serving |
| `backend/app/services/colors.py` | Colour sources: The Color API, Colormind, logo extraction, SSRF-safe reference-site analysis, industry palettes, colour harmonies, contrast balancing |
| `backend/app/sitegen/spec.py` | Site spec schema + the only allowed edit operations (validated) |
| `backend/app/sitegen/render.py` | Spec → static site renderer (fonts, logo, sitemap, robots) |
| `backend/app/sitegen/art.py` | Generated SVG hero art, maps, gradient mesh, patterns; icon helper |
| `backend/app/sitegen/icons.json` | 71 Lucide icons (ISC licence) |
| `backend/app/sitegen/LUCIDE-LICENSE.txt` | Icon licence |
| `backend/app/sitegen/templates/v2/page.html.j2` | Page layout (header, nav, footer, sticky call bar) |
| `backend/app/sitegen/templates/v2/sections.html.j2` | Section library: 15 section types with variants |
| `backend/app/sitegen/templates/v2/site.css.j2` | Design system stylesheet for all themes and variants |
| `backend/app/sitegen/fonts/*.woff2` + `OFL-LICENSE.txt` | 9 open-licence typefaces (Inter, Manrope, Space Grotesk, Plus Jakarta Sans, Nunito, Outfit, Fraunces, Source Serif 4, DM Serif Display) |
| `tests/test_team.py` | Tests: follow-up questions, knowledge propagation, varied designs, change requests, spec validation, critic |
| `tests/test_colors.py` | Tests: colour APIs (mocked), outage handling, SSRF guard, logo/site extraction, custom colours, template switch |
| `CHANGES.md` | This file |

## Modified files (19)

| File | What changed |
|---|---|
| `backend/app/agents/orchestrator.py` | Runs the creative team with the knowledge board; adaptive follow-up questions; brand scout (logo / reference site → suggestions); versioned site specs; re-applies owner edits when the plan is regenerated; security-relevant fields always confirmed |
| `backend/app/agents/llm.py` | Adds the vision call used by the visual critic |
| `backend/app/agents/qa.py` | QA sandbox serves production security headers; stubs platform API calls |
| `backend/app/agents/security_privacy.py` | Allows bundled `.woff2` fonts; JSON-LD isn't counted as an inline script |
| `backend/app/onboarding/schema.py` | Adaptive trade-specific follow-up questions; new questions: website you like, colours (palette picker, custom hex), starting design (template) |
| `backend/app/models.py` | New tables `KnowledgeEntry`, `SiteSpec`; `Build.spec_id` |
| `backend/app/services/release.py` | Builds render from the site spec and run the visual-critic loop before QA/security |
| `backend/app/api/release_api.py` | Change-request, knowledge-trail and spec-history endpoints; critic report in builds; `font-src 'self'` in site CSP |
| `backend/app/api/projects.py` | Question endpoint supports adaptive questions |
| `backend/app/main.py` | Registers the design API |
| `backend/app/security.py` | Secret-scanner patterns require a word boundary (font names were flagged as API keys) |
| `frontend/app.js` | Colour picker, template gallery, change-request box, design-review panel, team reasoning trail, true-width desktop preview |
| `frontend/app.css` | Styles for the above |
| `tests/conftest.py` | Disables live colour APIs in tests; helper answers phone questions |
| `tests/test_security.py` | Font-name false-positive regression test |
| `tests/e2e_browser.py` | Covers follow-ups, pickers, change requests, design switch |
| `requirements.txt` | Adds Pillow |
| `README.md` | New sections: agent collaboration, colours & templates |
| `.gitignore` | Excludes every `data/` folder and `.secret_key` |

## Unchanged from v1

`config.py`, `db.py`, `deps.py`, `audit.py`, `lifecycle.py`, `policy.py`, `agents/backend_data.py`,
`agents/contracts.py`, `agents/design.py`, `agents/discovery.py`, `agents/planning.py`, `api/auth.py`, `api/owner.py`,
`api/public.py`, `services/hosting.py`, `services/jobs.py`, `services/notify.py`, `sitegen/generator.py` (now only used
for checksums and diffs), the v1 templates in `sitegen/templates/` (kept, unused by new builds),
`frontend/index.html`, `run.sh`, `tests/test_leads.py`, `tests/test_onboarding.py`, `tests/test_release.py`.

## Removed

`backend/data/.secret_key` (see the top of this file).
