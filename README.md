# Consolidated dbt Docs

Merges documentation artifacts from multiple dbt projects and Power BI into a single unified lineage site, deployed to GitHub Pages.

## Architecture

```
┌──────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
│ dbt-finance-     │   │ dbt-retail-banking-  │   │ dbt-treasury-risk-   │
│ foundation       │   │ mart                 │   │ mart                 │
└───────┬──────────┘   └───────────┬──────────┘   └───────────┬──────────┘
        │                          │                           │
        │  repository_dispatch     │  repository_dispatch      │  repository_dispatch
        ▼                          ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        consolidated-dbt-docs (this repo)                     │
│                                                                             │
│  1. Download artifacts from each source repo                                │
│  2. Merge manifests + catalogs (cross-project lineage stitching)            │
│  3. Inject Power BI exposures from TMDL metadata                            │
│  4. Deploy unified site to GitHub Pages                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Quick Start (Fork & Configure)

### 1. Fork all 5 repos

- [dbt-finance-foundation](https://github.com/praveenvenugopalr/dbt-finance-foundation)
- [dbt-retail-banking-mart](https://github.com/praveenvenugopalr/dbt-retail-banking-mart)
- [dbt-treasury-risk-mart](https://github.com/praveenvenugopalr/dbt-treasury-risk-mart)
- [powerbi-finance-report](https://github.com/praveenvenugopalr/powerbi-finance-report)
- [consolidated-dbt-docs](https://github.com/praveenvenugopalr/consolidated-dbt-docs) (this repo)

### 2. Create a Personal Access Token (PAT)

1. Go to **GitHub Settings → Developer settings → Fine-grained tokens**
2. Create a token with:
   - **Repository access**: Select all 5 repos
   - **Permissions**:
     - `Contents: Read` (to download artifacts)
     - `Actions: Read` (to access workflow artifacts)
3. Copy the token value

### 3. Add the secret to each source repo

In **each** of the 4 source repos (3 dbt + 1 Power BI), go to:

**Settings → Secrets and variables → Actions → New repository secret**

- Name: `DOCS_DISPATCH_TOKEN`
- Value: (paste your PAT)

Also add the same secret to **this** repo (consolidated-dbt-docs) — it's used to download cross-repo artifacts.

### 4. Enable GitHub Pages

In this repo, go to **Settings → Pages**:
- Source: **GitHub Actions**

### 5. Trigger a build

Push a change to any source repo, or manually trigger via **Actions → Rebuild Consolidated Docs → Run workflow**.

## How It Works

### Source Repos (on push to main)
1. Run `dbt docs generate` (produces `manifest.json` + `catalog.json`)
2. Upload artifacts via `actions/upload-artifact`
3. Fire `repository_dispatch` event to this repo

### This Repo (on `repository_dispatch` or manual trigger)
1. Downloads latest artifacts from all source repos using `dawidd6/action-download-artifact`
2. Runs `scripts/merge_docs.py --env prod`:
   - Merges all manifests into one (nodes, sources, macros, exposures)
   - Stitches cross-project lineage (source → upstream model)
   - Injects Power BI exposures (from `pbi_metadata.json`)
3. Runs `scripts/inject_switcher.py` (adds DEV/PROD environment toggle)
4. Deploys to GitHub Pages

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/merge_docs.py` | Merges multi-project artifacts, stitches lineage, injects PBI exposures |
| `scripts/parse_pbi.py` | Parses Power BI TMDL files to extract tables, columns, measures, relationships |
| `scripts/inject_switcher.py` | Adds DEV/PROD environment toggle to the dbt docs UI |

## Customizing for Your Projects

1. Edit `.github/workflows/rebuild_docs.yml`:
   - Update the `repo:` fields to point to your forked/own dbt project repos
   - Update artifact names to match your source repo workflow artifact names
2. If you have more or fewer dbt projects, add/remove download steps accordingly
3. Power BI is optional — remove the download step and the PBI section in `merge_docs.py` if not needed

## Live Demo

https://praveenvenugopalr.github.io/consolidated-dbt-docs/
