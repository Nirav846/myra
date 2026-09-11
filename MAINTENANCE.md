# MYRA Maintenance Rules

## Dependency Security SLA

| Severity | Fix Window | Action |
|----------|-----------|--------|
| **Critical** | **48 hours** | Fix immediately. Do not defer to a batch. Create PR, run tests, merge. If no fix exists upstream, mitigate (pin, override, or remove the dependency). |
| **High** | 1 week | Batch with other updates. If 5+ high-severity items accumulate, treat as critical. |
| **Moderate/Low** | Next scheduled batch | Dependabot groups these into PRs automatically. Merge when CI passes. |

### Process

1. Dependabot creates PRs daily (npm + pip).
2. Patch/minor security PRs that pass CI are auto-merge candidates (enable via GitHub settings → "Auto merge" for Dependabot PRs).
3. Major-version bumps require manual review — do not auto-merge.
4. Weekly CI audit job (Monday 06:00 UTC) runs `pip-audit` + `npm audit` independently of Dependabot, catching any coverage gaps.
5. When a critical alert is filed, fix it within 48 hours. The fix window is measured from when the alert appears on the Dependabot dashboard, not from when someone notices it.

### Why This Exists

The 9-alert backlog (2 critical, 2 high, 5 moderate) that was resolved on 2026-09-10 accumulated across multiple deferred rounds. Critical vulnerabilities should not wait for a natural lull between feature work.
