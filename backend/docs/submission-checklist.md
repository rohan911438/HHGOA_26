# HHGoa'26 Submission Checklist

**Deadline: Sept 24, 2026, 11:59 PM IST.** One submission per team, by the team lead, at https://forms.gle/yxXzqSULGgZ9VUF56. No resubmissions.

Legend: ✅ done and verified in this repository · ⏳ needs a human action · ❌ not done

## Required by the challenge

| Item | Status | Evidence |
|---|---|---|
| Working agent | ✅ | Official pipeline `python -m app.benchmark.runner --official`; Phase-2 API + dashboard live (links below) |
| GitHub repository | ⏳ | Commit and push the new files (see "Before submitting"). Nothing has been committed yet. |
| Agent output on the 20 provided cases | ✅ | `cases/HHG-001.json` … `cases/HHG-020.json`, one per case, README answer format, 20/20 conformant |
| Case: investigation record, evidence, findings, decisions, actions | ✅ | `case` object in each answer; details in `backend/benchmark/results/official/<id>/` |
| Case written to the graph | ✅ | 20 `InvestigationCase` vertices in TigerGraph graph `HHGOA_IEEE`, each read back and verified (`graph-verification.json`) |
| SAR when required by policy | ✅ | 5 filed (HHG-005, 006, 010, 014, 019) under §3a; 15 not filed, reason recorded |
| NBA + approval route **before** additional evidence | ✅ | `next_best_actions.initial`, 20/20, routes = policy §2 |
| NBA + approval route **after** additional evidence | ✅ | `next_best_actions.final` + `what_changed`, 20/20; 12 changed after evidence |
| 3–5 minute demo video | ⏳ | Link to be added: `<DEMO_VIDEO_URL>` |
| Technical blog post | ⏳ | Draft ready: `backend/docs/technical-blog.md`. Publish it and add `<BLOG_URL>` |
| X or LinkedIn post, linking blog or demo | ⏳ | Drafts ready: `backend/docs/x-post.md`. Post it and record the URL |
| Post tags @TigerGraphDB | ⏳ | Tag included in the drafts; confirm it on the published post |

## Final quality

| Item | Status | Evidence |
|---|---|---|
| README | ✅ | "Official HHGOA Benchmark" section; dev fallback vs. official clearly separated |
| Benchmark report | ✅ | `backend/docs/official-benchmark-report.md` |
| Judging checklist | ✅ | `backend/docs/phase-2m-demo-and-judging-checklist.md` (benchmark evidence under each criterion) |
| Deployment | ✅ | Dashboard https://hhgoa-fraud-frontend.vercel.app · API https://hhgoa-fraud-backend.onrender.com (development graph; cold start ~1 min) |
| API | ✅ | Unchanged; offline API tests pass |
| Tests: backend offline | ✅ | 365/365 (`pytest tests/unit tests/api`) |
| Tests: frontend | ✅ | 31/31 · `tsc --noEmit` clean · ESLint clean · `next build` OK |
| Security audit | ✅ | No secrets in tracked files, answers, results or docs (see the final report) |
| No secrets | ✅ | `.env` and `.env.local` gitignored; the official dataset folder gitignored |
| No fabricated results | ✅ | No accuracy claimed (no answer key); run history including wrong runs archived |

## Before submitting (human steps)

1. Review `git status`, then commit and push. **Suggested:** `cases/`, `backend/app/benchmark/official/`, `backend/tests/unit/test_official_benchmark.py`, `backend/benchmark/results/official/`, `backend/docs/*`, `README.md`, `.gitignore`, `backend/pyproject.toml`, `backend/app/benchmark/runner.py`. **Do not commit** `backend/data/hhgoa_ieee/` (it's gitignored).
2. Record and upload the 3–5 minute demo. Suggested extra beat: open `cases/HHG-014.json` and show the device traced to 27 cards, the before/after actions, and the SAR.
3. Publish the blog, then replace `<GITHUB_URL>`, `<DEMO_VIDEO_URL>` and `<BLOG_URL>` in `technical-blog.md` and `x-post.md`.
4. Post on X/LinkedIn tagging @TigerGraphDB.
5. Submit the form before **23:59 IST**, team lead only, once.

## Known limitations to be upfront about

- Accuracy is unknown: the package has no answer key.
- Customer replies are simulated by one stated rule (README §5 allows this).
- The probability weights are hand-set; the official path uses no LLM (`tokens: 0`).
- Case memory is cited but not weighted (HHG-009 example in the report).
