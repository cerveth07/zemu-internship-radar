# Reused collector components

Only the listed extraction modules are included; their original MIT license texts are preserved beside them.

| Source | Pinned commit | Files / use |
| --- | --- | --- |
| [Dongnb66/internship-workbench](https://github.com/Dongnb66/internship-workbench) | `f51fa72dc9de074bdfac11e138ca1e2d47a65715` | `extension/collector.js`, `crawler/lib/normalize.mjs`, `crawler/lib/stopRules.mjs`: DOM extraction, list/detail merging and visible access-wall detection. Copied unchanged. |
| [JiemsLBJ/offer-harvester](https://github.com/JiemsLBJ/offer-harvester) | `cd0086ea48786c0216e6a5905be613bcfa8471cd` | `helpers.ts` HTML/entity/date helpers adapted to JavaScript in `text.mjs`. No Bun dependency or Node execution of remote page scripts. |

Radar supplies task ownership, city/page parameters, atomic per-task snapshots, graceful stop and desktop packaging in `tools/shixiseng_runner.mjs` / `app/shixiseng_runtime.py`. Playwright Core is pinned separately in `tools/shixiseng-node/package-lock.json`.
