---
name: HN Digest Frontend
overview: Create a GitHub Pages frontend in `docs/` with a Hacker News-inspired theme, a monthly calendar sidebar, and a markdown digest viewer -- powered by a single `index.html` and a generated manifest.
todos:
  - id: create-index-html
    content: Create docs/index.html with HN-themed calendar + markdown viewer (marked.js CDN, vanilla JS)
    status: completed
  - id: seed-docs-digests
    content: Copy existing 35 digests into docs/digests/ and generate manifest.json
    status: completed
  - id: update-workflow
    content: Add sync step in digest.yml to copy digests to docs/ and regenerate manifest on each run
    status: completed
  - id: enable-gh-pages
    content: Enable GitHub Pages from docs/ on main branch via gh CLI
    status: completed
  - id: test-site
    content: Push, verify Pages deploys, and check calendar + digest rendering in browser
    status: pending
isProject: false
---

# HN Digest -- GitHub Pages Frontend

## Problem

GitHub Pages only serves files from the configured source folder (`docs/`). Digests live in `digests/daily/`. We need both the frontend and the digest content accessible from `docs/`.

## Approach

Keep the existing digest generation untouched. Add a workflow step that copies all digests into `docs/` and builds a JSON manifest. The frontend is a single `docs/index.html` -- no build tools, no framework, just vanilla HTML/CSS/JS with a CDN-loaded markdown renderer.

## What gets created

### 1. `docs/index.html` -- Single-page app

- **Left panel**: Monthly calendar (navigate months with arrows). Days with a digest are clickable (highlighted). Today is marked.
- **Right panel**: Rendered digest markdown.
- **Theme**: Hacker News inspired -- `#ff6600` accent, off-white background (`#f6f6ef`), clean sans-serif type, minimal chrome. Same warmth and simplicity as news.ycombinator.com.
- **Markdown rendering**: Use [marked.js](https://cdn.jsdelivr.net/npm/marked/marked.min.js) from CDN (zero dependencies).
- **Data source**: Reads `digests/manifest.json` on load to know which dates have digests, then fetches `digests/YYYY-MM-DD.md` on click.

### 2. `docs/digests/manifest.json` -- Date index

A simple JSON array of available dates:
```json
["2026-03-25", "2026-03-26", "2026-04-29"]
```

### 3. Workflow change in [`.github/workflows/digest.yml`](.github/workflows/digest.yml)

Add a step **after** the OpenCode generation and **before** the commit that:
- Copies all `digests/daily/*.md` into `docs/digests/` (flat, no `daily/` nesting)
- Regenerates `docs/digests/manifest.json` from the file listing
- Adds `docs/` to the `git add`

```bash
# Sync digests to docs
mkdir -p docs/digests
cp digests/daily/*.md docs/digests/
ls docs/digests/*.md | sed 's|.*/||;s|\.md||' | sort | jq -R . | jq -s . > docs/digests/manifest.json
```

The commit step changes from:
```
git add digests/daily/ README.md
```
to:
```
git add digests/daily/ README.md docs/
```

### 4. GitHub Pages config

Enable GitHub Pages from `docs/` on `main` branch (done via repo settings or `gh api`).

## What does NOT change

- `digests/daily/` remains the source of truth (agent writes here)
- Agent prompt ([`.opencode/agents/digest.md`](.opencode/agents/digest.md)) -- untouched
- [`AGENTS.md`](AGENTS.md) -- untouched
- [`opencode.json`](opencode.json) -- untouched

## UI Wireframe

```
+------------------+---------------------------------------------+
|   << Apr 2026 >> |                                             |
|  Mo Tu We Th Fr  |  # Hacker News Digest -- 2026-04-29        |
|      1  2  3  4  |                                             |
|   7  8  9 10 11  |  ## Top Stories                             |
|  14 15 16 17 18  |                                             |
|  21 22 23 24 25  |  ### Ghostty is leaving GitHub              |
|  28 [29]         |  Mitchell Hashimoto announced...            |
|                  |                                             |
|                  |  ### GitHub RCE Vulnerability               |
|                  |  Wiz researchers uncovered...               |
+------------------+---------------------------------------------+
       calendar                  digest content
```

Days with digests get the orange dot/highlight. `[29]` = selected/today.
