# Runbook — Vercel project setup for the landing page

**When to run**: once, after `main` has the `landing/` directory (i.e. after
Allen merges `docs/readme-landing-v0` + `devops/v0-pipeline` into main).

## Goal

Vercel project at `agibridge.dev` (Phase A registration) that:

1. Serves only the `landing/` directory (Root Directory = `landing/`).
2. Deploys ONLY when commits land on `main` (no preview-per-branch — brief
   hard constraint, F-1 framing).
3. Redeploys on every push to `main` that touches `landing/` (DoD #3).

## Source-grounded references

- Root Directory:
  <https://vercel.com/docs/deployments/configure-a-build#root-directory>
- `git.deploymentEnabled` (per-branch enable/disable):
  <https://vercel.com/docs/project-configuration/git-configuration#git.deploymentenabled>
- Ignored Build Step (path-filtered builds):
  <https://vercel.com/docs/project-configuration/project-settings#ignored-build-step>

## Steps (Vercel dashboard, ~5 min)

1. **Create project** → Import `allenwu-blip/agibridge` from GitHub.
2. **Set Root Directory** → `landing` (Settings → Build & Development).
3. **Set Framework Preset** → "Other" (the landing is a single MDX file +
   minimal static site; if Tech Writer adds Next.js / Astro later, switch
   accordingly).
4. **Custom Domain** → add `agibridge.dev` (Phase A registration).
5. **Verify `vercel.json` is honored**: the file at `landing/vercel.json` (in
   this repo) sets `git.deploymentEnabled` to disable all branches except
   main. Vercel reads this on every deploy; no dashboard step needed for the
   branch filter.
6. **Ignored Build Step** (Settings → Build & Development → Ignored Build
   Step). Pick **"Only build if there are changes in a folder"** and enter
   `landing` as the folder. Per Vercel docs (linked above), this exits 0 (=
   skip build) when `git diff` shows no changes under that path.

## Verification (DoD #3)

```bash
# 1. Touch landing/, push to main, watch Vercel rebuild.
git checkout main && git pull
echo "<!-- bump $(date -u +%s) -->" >> landing/index.mdx
git commit -am "test: trigger Vercel redeploy"
git push origin main
# Open https://vercel.com/<team>/agibridge/deployments — newest deployment
# should be in flight with the trigger commit's SHA.

# 2. Touch a non-landing path, push, confirm NO Vercel rebuild.
echo "# noop" >> README.md
git commit -am "test: confirm Vercel ignores non-landing changes"
git push origin main
# Vercel deployments page should show "Ignored Build Step" / "Canceled" for
# the new commit.
```

## Anti-patterns (from the brief)

- **Do NOT** set Vercel preview deployments per branch — generates URLs that
  violate the F-1 "ephemeral, no accounts, no storage" framing if accidentally
  indexed.
- **Do NOT** combine landing with the HF Space (Phase D A3.1 hard-locked
  separate Vercel for cold-start UX reasons).
- **Do NOT** add Vercel Analytics — F-1 forbids analytics SDKs.
