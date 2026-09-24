# Official HHGOA Benchmark Results

*HHGOA_IEEE, 20-case benchmark. Run on 2026-09-24.*

## 1. Executive summary

| | |
|---|---|
| Dataset | **HHGOA_IEEE**, the official package from TigerGraph's Drive folder linked in the challenge brief ("TigerGraph Agentic Fraud Investigation HHGOA", Dataset section) |
| Package validation | 23/23 checks pass (see §2) |
| Benchmark cases | **20** (`HHG-001` … `HHG-020`, from `case_pack.csv`) |
| Cases executed | **20 / 20** |
| Answer files written (`cases/<case_id>.json`) | **20 / 20** |
| Cases written to TigerGraph **and read back** | **20 / 20** |
| Answers fully conformant with the README answer format and Fraud Policy | **20 / 20** |
| Initial and final next-best action + approval route recorded | **20 / 20** |
| Additional evidence requested | 12 / 20 (the recommendation changed after the evidence in all 12) |
| SARs filed | 5 / 20 |
| **Accuracy against the official answers** | **NOT AVAILABLE IN OFFICIAL BENCHMARK SCHEMA.** The package ships no answer key: *"Submit the 20 answer files. We score them against an answer key you don't have."* (dataset README, "The task") |

Every number in this report comes from `backend/benchmark/results/official/summary.json` and the per-case files beside it. No accuracy, pattern-accuracy, NBA-accuracy, approval-accuracy or SAR-accuracy figure is given, because none can be computed without the key. Section 6 lists what *was* measured instead.

## 2. Dataset

**Source.** `https://drive.google.com/drive/folders/1YDJUW1fiE7Jx8R9KqknC4IcsED9zll2A`. The folder is named `HHGOA_IEEE`, is owned by a `@tigergraph.com` account, and is linked from the official brief. It contains five files, all downloaded unmodified to `backend/data/hhgoa_ieee/` (gitignored, never committed):

| File | Rows | README says |
|---|---:|---|
| `README.md` | n/a | task, columns, patterns, regulatory refs, **Fraud Policy v1.0**, **Answer Format**, the 20 cases |
| `transactions.csv` | 590,742 | 590,742 ✔ |
| `identity.csv` | 144,432 | 144,432 ✔ |
| `closed_cases_history.csv` | 5,565 (4,665 confirmed fraud, 900 cleared) | 5,565 / 4,665 / 900 ✔ |
| `case_pack.csv` | 20 | 20 ✔ |

**Validation** (`python -m app.benchmark.official validate`, which writes `benchmark/results/official/manifest.json`) checks that all 5 files are present; the README contains the policy, pattern, regulatory, answer-format and case sections; the case-pack and closed-case headers match the README exactly; `transactions.csv` has the four added columns and **no fraud label**; exactly 20 unique `HHG-###` case IDs; unique flagged transactions; known trigger types; card IDs well-formed and belonging to the stated customer; risk score present only for risk-score triggers; the CSV agrees with the README's case table; all 20 flagged transactions exist with the stated customer; and all row counts match. Result: **23/23 pass.**

**What the package defines** (the terms below are the package's own):
- Five documented patterns: `card_testing`, `card_not_present_fraud`, `card_not_present_new_device`, `out_of_region_use`, `account_takeover`. Plus `undocumented` and `none`.
- Fraud Policy v1.0: 14 actions, approval routes `auto` / `L1` / `L2`, rules R1–R10, §3a (case vs. report), §3b (the recommendation may change), §4 exposure, §5 simulated evidence, §6 stopping, §7 explaining.
- Answer format: one JSON per case with `case`, `evidence_requests`, `next_best_actions.initial/final/what_changed`, `sar`, `stop_reason`, `tool_calls`, `tokens`, `latency_s`.
- Regulatory references: FinCEN, FATF, FFIEC and OFAC links (listed in the README; not loaded, see §10).

**A fact the README does not state, measured here.** Card IDs look like `C01234-K1`, but the README doesn't say how `-K#` is derived. `customer_id + "-K" + rank of card6 (card type) sorted alphabetically within the customer` reproduces **14,975 / 14,975** labelled transaction→card pairs in `closed_cases_history.csv` and `case_pack.csv`. Other orderings reproduce about 34%. `prepare` re-checks this rule on every run and refuses to load if it drops below 100%.

**Separation from development data.** `backend/data/hhgoa/` still holds the Kaggle IEEE-CIS development fallback, which carries `isFraud`. The official pipeline never reads it. The README says using the original public files to recover outcomes means disqualification, and the official graph is loaded only from the official package.

## 3. Methodology

```
python -m app.benchmark.official fetch        # official Drive folder only
python -m app.benchmark.official validate     # 23 checks -> manifest.json
python -m app.benchmark.official prepare      # slim load files, card-id rule re-verified
python -m app.benchmark.official load-graph   # HHGOA_IEEE graph + GSQL loading jobs
python -m app.benchmark.runner --official     # validate -> 20 cases -> conformance -> summary
```

For each case, in `opened_at` order so that case memory builds up chronologically:

1. **Trigger:** read the case-pack row.
2. **Open the case:** fetch the flagged transaction from TigerGraph.
3. **History:** all transactions on the card, and the customer's other cards.
4. **Device neighbourhood:** the flagged device profile's cards within ±30 days, its all-time footprint (cards, transactions, share marked `New`, share behind an anonymous or hidden proxy), and confirmed-fraud closed cases that involve it.
5. **Memory:** closed cases on the customer's cards; closed cases with the same pattern in a similar exposure band that closed *before* this case opened; and the agent's own earlier `InvestigationCase` vertices linked by card or by a non-mass-market device.
6. **Assess:** named signals, then a fraud probability and the independent lines of evidence.
7. **Initial next-best action** under R1–R10, with the §2 route attached by one function (`policy.route`).
8. **Evidence request** when §6 doesn't allow stopping (R1/§3b). The reply is simulated and stated (README §5).
9. **Re-assess**, then the **final next-best action** and `what_changed`.
10. **SAR decision** by §3a, and the narrative when filed.
11. **Write the case to TigerGraph, then read it back** and compare it field by field.
12. **Conformance checks** on the answer file (§6).

Every step is recorded in `benchmark/results/official/<case_id>/trace.json`, including every graph call with its latency.

## 4. Architecture used

```
case_pack.csv row (official)
        ↓
OfficialInvestigator            app/benchmark/official/agent.py
        ↓  (8–9 counted tool calls per case)
OfficialGraphTools              app/benchmark/official/tools.py  — 7 GSQL interpreted queries + 1 write
        ↓
TigerGraph  graph HHGOA_IEEE    590,742 HTxn · 14,317 HCard · 13,553 HCustomer · 9,705 HDeviceProfile
                                5,565 HClosedCase · InvestigationCase (agent output)
        ↓
Signals (evidence)              app/benchmark/official/signals.py  — pure functions, thresholds cited
        ↓
Uncertainty                     agent.assess — log-odds probability + independent-evidence groups
        ↓
Context / memory                closed cases + the agent's earlier InvestigationCase vertices (graph)
        ↓
Policy                          app/benchmark/official/policy.py — Fraud Policy v1.0, exact identifiers
        ↓
Case + SAR                      narrative.py → cases/<case_id>.json → written to graph → read back
```

The `HHGOA_IEEE` graph lives alongside the development `HHGOA_FRAUD` graph on the same TigerGraph Savanna workspace. Its types are prefixed `H` so they can't collide with it. `HHGOA_FRAUD`, the deployed API and the frontend are untouched.

**Why a separate pipeline rather than the Phase-2 `AgentOrchestrator`.** The Phase-2 agent was built before the official package existed. Its evidence and uncertainty layers read the Kaggle `is_fraud` label (`app/evidence/normalize.py`, `app/uncertainty/engine.py`), which the official data doesn't have. Its policy vocabulary (`REQUEST_MORE_EVIDENCE`, etc.) is a development heuristic, not the official action set. Running it unchanged on official IDs would either fail on missing fields or emit non-policy action names. The official pipeline is additive: it reuses the TigerGraph client, the credential handling and the transient-retry approach, and follows the same "facts from the graph, decisions from rules, everything traceable" design.

## 5. Case-by-case results

`p` is the agent's fraud probability before and after the (simulated) evidence. Routes come from policy §2.

| Case | Trigger | Verdict | Pattern | p initial → final | Initial NBA (route) | Evidence requested | Final NBA (route) | SAR | Exposure $ | Graph | Conformance |
|---|---|---|---|---|---|---|---|---|---:|---|---|
| HHG-001 | risk_score | legitimate | none | 0.32 → 0.02 | VERIFY_WITH_CUSTOMER auto, CREATE_CASE auto | customer_validation | CREATE_CASE auto, CLOSE_NO_FRAUD auto | no | 0.00 | written + read back | 36/36 |
| HHG-002 | risk_score | fraud | card_not_present_fraud | 0.56 → 0.85 | VERIFY_WITH_CUSTOMER auto, CREATE_CASE auto | customer_validation | BLOCK_CARD L1, CREATE_CASE auto | no | 292.36 | written + read back | 35/35 |
| HHG-003 | customer_report | uncertain | none | 0.5 → 0.5 | BLOCK_CARD L1, CREATE_CASE auto, ESCALATE_TO_ANALYST auto | none | BLOCK_CARD L1, CREATE_CASE auto, ESCALATE_TO_ANALYST auto | no | 49.00 | written + read back | 35/35 |
| HHG-004 | customer_report | fraud | card_not_present_new_device | 0.93 → 0.93 | BLOCK_CARD L1, CREATE_CASE auto | none | BLOCK_CARD L1, CREATE_CASE auto | no | 305.06 | written + read back | 35/35 |
| HHG-005 | risk_score | fraud | undocumented | 0.92 → 0.92 | BLOCK_CARD L1, CREATE_CASE auto, FILE_REPORT L2, MONITOR_CONNECTED_CARDS auto, ESCALATE_TO_ANALYST auto | none | BLOCK_CARD L1, CREATE_CASE auto, FILE_REPORT L2, MONITOR_CONNECTED_CARDS auto, ESCALATE_TO_ANALYST auto | yes | 100.07 | written + read back | 39/39 |
| HHG-006 | customer_report | fraud | undocumented | 0.98 → 0.98 | BLOCK_CARD L1, CREATE_CASE auto, FILE_REPORT L2, ESCALATE_TO_ANALYST auto | none | BLOCK_CARD L1, CREATE_CASE auto, FILE_REPORT L2, ESCALATE_TO_ANALYST auto | yes | 1,906.07 | written + read back | 39/39 |
| HHG-007 | risk_score | legitimate | none | 0.43 → 0.04 | VERIFY_WITH_CUSTOMER auto, CREATE_CASE auto | customer_validation | CREATE_CASE auto, CLOSE_NO_FRAUD auto | no | 0.00 | written + read back | 36/36 |
| HHG-008 | customer_report | legitimate | none | 0.23 → 0.01 | CREATE_CASE auto, STEP_UP_AUTH auto, MONITOR_CARD auto, ESCALATE_TO_ANALYST auto | step_up_auth | CREATE_CASE auto, CLOSE_NO_FRAUD auto | no | 0.00 | written + read back | 36/36 |
| HHG-009 | customer_report | legitimate | none | 0.23 → 0.01 | CREATE_CASE auto, STEP_UP_AUTH auto, MONITOR_CARD auto, ESCALATE_TO_ANALYST auto | step_up_auth | CREATE_CASE auto, CLOSE_NO_FRAUD auto | no | 0.00 | written + read back | 36/36 |
| HHG-010 | risk_score | fraud | card_not_present_new_device | 0.76 → 0.93 | DECLINE_TRANSACTION L1, VERIFY_WITH_CUSTOMER auto, CREATE_CASE auto | customer_validation | BLOCK_CARD L1, CREATE_CASE auto, FILE_REPORT L2 | yes | 1,000.03 | written + read back | 39/39 |
| HHG-011 | customer_report | fraud | card_not_present_new_device | 0.83 → 0.83 | BLOCK_CARD L1, CREATE_CASE auto | none | BLOCK_CARD L1, CREATE_CASE auto | no | 131.30 | written + read back | 35/35 |
| HHG-012 | risk_score | legitimate | none | 0.31 → 0.02 | VERIFY_WITH_CUSTOMER auto, CREATE_CASE auto | customer_validation | CREATE_CASE auto, CLOSE_NO_FRAUD auto | no | 0.00 | written + read back | 36/36 |
| HHG-013 | risk_score | fraud | card_not_present_new_device | 0.6 → 0.87 | VERIFY_WITH_CUSTOMER auto, CREATE_CASE auto | customer_validation | BLOCK_CARD L1, CREATE_CASE auto | no | 35.66 | written + read back | 35/35 |
| HHG-014 | analyst_request | fraud | undocumented | 0.88 → 0.88 | BLOCK_CARD L1, CREATE_CASE auto, FILE_REPORT L2, MONITOR_CONNECTED_CARDS auto, ESCALATE_TO_ANALYST auto | none | BLOCK_CARD L1, CREATE_CASE auto, FILE_REPORT L2, MONITOR_CONNECTED_CARDS auto, ESCALATE_TO_ANALYST auto | yes | 74.96 | written + read back | 39/39 |
| HHG-015 | risk_score | fraud | card_not_present_new_device | 0.77 → 0.94 | DECLINE_TRANSACTION L1, VERIFY_WITH_CUSTOMER auto, CREATE_CASE auto | customer_validation | BLOCK_CARD L1, CREATE_CASE auto | no | 599.94 | written + read back | 35/35 |
| HHG-016 | customer_report | fraud | card_not_present_new_device | 0.69 → 0.91 | CREATE_CASE auto, STEP_UP_AUTH auto, MONITOR_CARD auto | step_up_auth | BLOCK_CARD L1, CREATE_CASE auto | no | 59.67 | written + read back | 35/35 |
| HHG-017 | risk_score | legitimate | none | 0.2 → 0.01 | VERIFY_WITH_CUSTOMER auto, CREATE_CASE auto | customer_validation | CREATE_CASE auto, CLOSE_NO_FRAUD auto | no | 0.00 | written + read back | 36/36 |
| HHG-018 | customer_report | uncertain | none | 0.55 → 0.55 | BLOCK_CARD L1, CREATE_CASE auto, ESCALATE_TO_ANALYST auto | none | BLOCK_CARD L1, CREATE_CASE auto, ESCALATE_TO_ANALYST auto | no | 39.08 | written + read back | 35/35 |
| HHG-019 | risk_score | fraud | undocumented | 0.93 → 0.93 | BLOCK_CARD L1, CREATE_CASE auto, FILE_REPORT L2, MONITOR_CONNECTED_CARDS auto, ESCALATE_TO_ANALYST auto | none | BLOCK_CARD L1, CREATE_CASE auto, FILE_REPORT L2, MONITOR_CONNECTED_CARDS auto, ESCALATE_TO_ANALYST auto | yes | 99.92 | written + read back | 39/39 |
| HHG-020 | risk_score | fraud | card_not_present_new_device | 0.73 → 0.92 | DECLINE_TRANSACTION L1, VERIFY_WITH_CUSTOMER auto, CREATE_CASE auto | customer_validation | BLOCK_CARD L1, CREATE_CASE auto | no | 125.08 | written + read back | 35/35 |

Full answer files: `cases/HHG-0NN.json`. Per-case artifacts in `backend/benchmark/results/official/HHG-0NN/`: `input.json`, `initial-investigation.json`, `initial-decision.json`, `additional-evidence.json`, `final-investigation.json`, `final-decision.json`, `case-record.json`, `graph-verification.json`, `sar.json`, `benchmark-comparison.json`, `trace.json`.

## 6. Aggregate results

**Measured (numerator / denominator)**

| Metric | Result |
|---|---|
| Cases executed | 20 / 20 = 100% |
| Answer files produced in the official format | 20 / 20 = 100% |
| Cases written to TigerGraph and read back identically | 20 / 20 = 100% |
| Answers passing every conformance check | 20 / 20 = 100% (35–39 checks per case, depending on the branch) |
| Initial and final NBA with approval route recorded | 20 / 20 = 100% |
| Cases that requested additional evidence | 12 / 20 |
| … whose recommendation changed after the evidence | 12 / 12 |
| SARs filed | 5 / 20 |
| Graph tool calls, total / median per case | 173 / 9 |
| Latency, median / max per case | 6.4 s / 66.1 s (HHG-008) in the final run. Latency is network-bound against Savanna and varies between runs: an earlier run of the same configuration measured 5.5 s / 175.7 s |
| LLM tokens | 0 (see §10) |

**Conformance checks.** These are deterministic and implemented in `app/benchmark/official/validate.py`:
- Every README field is present with valid enums.
- Probability is in [0, 1].
- `pattern_description` is present if and only if the pattern is `undocumented`.
- All affected transaction, card, device-profile, closed-case and evidence IDs exist in the dataset.
- `exposure_usd` equals the sum of the absolute amounts of the affected transactions (§4).
- `first_suspicious_txn_id` is one of the affected transactions.
- Legitimate verdicts have no affected transactions, zero exposure and no SAR.
- Every action is a policy action, and every route equals `policy.route(action, exposure)` (§2).
- Every reason cites a rule.
- No evidence requested means `final == initial`.
- `CREATE_CASE` is present whenever evidence is requested (§3a).
- No `BLOCK_ALL_CARDS` (R10).
- `sar.file` matches the presence of `FILE_REPORT`.
- A filed SAR has 6–12 sentences, existing subjects, `total_amount_usd` equal to exposure, well-formed dates and a `fraud` verdict. An unfiled SAR has the empty-field form.
- `written_to_graph` is true with a `graph_case_id`.

**Not available in the official benchmark schema** (no answer key in the package):

| Metric | Value |
|---|---|
| Investigation accuracy | NOT AVAILABLE IN OFFICIAL BENCHMARK SCHEMA |
| Pattern accuracy | NOT AVAILABLE IN OFFICIAL BENCHMARK SCHEMA |
| Initial NBA accuracy | NOT AVAILABLE IN OFFICIAL BENCHMARK SCHEMA |
| Final NBA accuracy | NOT AVAILABLE IN OFFICIAL BENCHMARK SCHEMA |
| Approval-route accuracy | NOT AVAILABLE IN OFFICIAL BENCHMARK SCHEMA. Routes are, however, 100% consistent with policy §2 for the actions chosen |
| Additional-evidence accuracy | NOT AVAILABLE IN OFFICIAL BENCHMARK SCHEMA |
| SAR accuracy | NOT AVAILABLE IN OFFICIAL BENCHMARK SCHEMA. `sar.file` agrees with `FILE_REPORT` in 20/20 |

**Observed output distribution** (a description of what the agent concluded, not a score): verdict fraud 12, legitimate 6, uncertain 2; pattern `card_not_present_new_device` 7, `undocumented` 4, `card_not_present_fraud` 1, `none` 8; status `closed_fraud` 8, `closed_legitimate` 6, `escalated` 6.

## 7. Next-best action, before and after evidence

- **Initial NBA recorded:** 20/20. **Final NBA recorded:** 20/20. **Approval route on every action:** 20/20.
- **Evidence requested (12).** `customer_validation` in 9 cases (HHG-001, 002, 007, 010, 012, 013, 015, 017, 020) and `step_up_auth` in 3 (HHG-008, 009, 016). In each, the initial NBA was a verify or step-up action (`auto`) plus `CREATE_CASE`, with `DECLINE_TRANSACTION` (`L1`) added when p ≥ 0.70. The final NBA followed from the stated simulated reply: `BLOCK_CARD` (`L1`) + `CREATE_CASE` (+ `FILE_REPORT` `L2` when §3a applied) after a denial, or `CREATE_CASE` + `CLOSE_NO_FRAUD` after a confirmation (R3).
- **No evidence requested (8), with the reason in `stop_reason`:**
  - Customer reports where the cardholder's own denial is the verification response and the graph corroborates it (HHG-004, 006, 011).
  - §6 stopping at p ≥ 0.85 on ≥ 2 independent lines of evidence (HHG-005, 014, 019).
  - Customer reports where the denial is the *only* evidence either way (HHG-003, 018). These are marked `uncertain`, with `BLOCK_CARD` under R2 and `ESCALATE_TO_ANALYST` under R8.
  - In all 8, `final == initial` and `what_changed == "nothing"`, as the README requires.

## 8. SAR results

5 filed (HHG-005, 006, 010, 014, 019); 15 not filed, each with the §3a reason recorded.
- **HHG-014 and HHG-019** (and HHG-005, on a second device): an `undocumented` shared-device scheme. HHG-014 was the analyst request about "the same unusual device profile" on several cards. The agent traced it from the flagged transaction to device profile `SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080` behind an anonymous proxy, and out to **27 other customers' cards** within 30 days. That is the same profile as the bank's closed ring cases CC-2649/2971/2985/3035. Filed under R6/R9 §3a, with `MONITOR_CONNECTED_CARDS` for every linked card.
- **HHG-006:** 4 online purchases between $400 and $500 within 30 minutes ($1,906.07), the just-under-$500 structuring scheme recorded in closed cases CC-3748/3841/3907/4086/4124. Filed under R9 and exposure > $1,000.
- **HHG-010:** card-not-present fraud from a device new to the account, with exposure $1,000.03. That exceeds the $1,000 threshold, so it's filed under R2/§3a.

Each narrative is built only from graph facts: who, when, where/channel, device, how, why (with the rule cited), and actions, in 6–12 sentences. Every subject ID exists in the dataset (checked).

## 9. Graph persistence

For each case the agent upserts an `InvestigationCase` vertex (`CASE-HHG-0NN`) with edges `CASE_FLAGGED`, `CASE_AFFECTS`, `CASE_ON_CARD`, `CASE_CONNECTED_CARD`, `CASE_DEVICE` and `CASE_SIMILAR_TO`. It then reads the case back with a separate GSQL query and compares `official_case_id`, `verdict`, `final_actions`, the flagged-edge count, the affected-edge count and the similar-case count. Only on an exact match does the answer say `written_to_graph: true` (`graph-verification.json` holds the evidence).

**Result: 20/20 written and verified.** An independent count after the run: `InvestigationCase` = 20 (IDs HHG-001…HHG-020), `CASE_FLAGGED` 20, `CASE_AFFECTS` 19, `CASE_ON_CARD` 20, `CASE_CONNECTED_CARD` 35, `CASE_DEVICE` 3, `CASE_SIMILAR_TO` 80.

Memory is read, not just written. Later cases query earlier agent cases by card and by non-mass-market device. In the final run this produced one hit: **HHG-009** (opened 2016-12-28) retrieved **CASE-HHG-005** (2016-12-08), because HHG-009's card `C08299-K1` was one of the cards HHG-005 placed under `MONITOR_CONNECTED_CARDS`. It is cited in HHG-009's evidence (`agent_case_memory` in `initial-investigation.json`).

## 10. Error analysis and run history

There is no answer key, so there is no case-level error analysis against expected answers. What follows is every problem found and how it was handled, including three full runs.

**Run 1 (archived: `run1-summary.json`, `run1-summary.csv`).** 19/20 executed. HHG-016 failed on a **DNS resolution error** reaching TigerGraph: infrastructure, not the application. It was recorded and re-run. Output: 16 fraud / 3 legitimate, 10 `undocumented`, 10 SARs. That contradicts the README's *"Half the cases are legitimate"*, so the signals were inspected against the raw data. Two genuine bugs turned up:
- **Shared-device "ring" over-fired.** The rule "≥ 2 other customers on the profile in 30 days" matched mass-market configurations used by **66–297 cards in 30 days**. Across the 9,705 profiles, the 95th percentile is 22 cards and the 99th is 112. The rule was replaced by the fingerprint of the ring the bank itself documented (CC-2649 group): the device is marked `New` on 100% of its transactions, sits behind an anonymous proxy on 100%, and averages 2.2 transactions per card. New rule: ≥ 2 other customers **and** New on ≥ 80% **and** ≤ 3 transactions per card. Profiles above the 95th percentile no longer link cases in memory.
- **Transparent proxies counted as suspicious.** `id_23` values are `IP_PROXY:TRANSPARENT/ANONYMOUS/HIDDEN`, and an exact match on `"transparent"` never matched.
- Also fixed: the validator didn't count official `HHG-*` IDs (legitimately cited when earlier agent cases are recalled) as dataset IDs, and one reason string lacked a rule citation.

**Run 2 (archived: `run2-summary.*`).** 20/20 executed. 15 fraud / 5 legitimate. Five answers had verdict `fraud` with pattern `none`, which the README's definitions make contradictory. Inspection showed:
- `device_status = Found` (the identity record saying the device is already known to the account) was ignored by the known-device signal. Fixed.
- Fraud confirmed only by the cardholder's denial of an online purchase now takes pattern 2/3 as the README defines them.
- In-person denials with no graph evidence either way are now `uncertain`, with R2 block + case and R8 escalation, instead of a coin flip at p = 0.50.

**Run 3 (final, reported above).** 20/20 executed, 20/20 conformant, 20/20 graph-verified. A repeat of the final configuration reproduced identical decisions for all 20 cases.

**Disclosure.** Between runs, the logic was changed after looking at the agent's own outputs on the 20 cases. No answers were available to fit to. Every change is a data-grounded bug fix: the thresholds come from the package's README and closed-case history, and each is cited in code. Still, the final numbers are not from a blind first run. The archived run-1 and run-2 summaries show exactly what changed.

**Infrastructure events** (none converted into success): two SSL EOF errors during the bulk load (the chunk was retried; upserts are idempotent); one DNS failure in run 1 (HHG-016); one DNS failure blocking a run start (retried after DNS resolved).

## 11. Limitations

- **No answer key.** Nothing here measures correctness. TigerGraph scores the answers privately.
- **Simulated replies decide 12 verdicts.** Per README §5, replies are not provided. The agent's single stated rule is that the assumed reply is the one its own probability favours (≥ 0.5 means deny or fail step-up; < 0.5 means confirm). That is transparent, but it means those final verdicts rest on the pre-evidence assessment.
- **Hand-set weights.** The probability model's log-odds weights come from the README's wording of signal strength, not fitted. `fraud_probability` is therefore not calibrated in the statistical sense, and calibration is scored. Fitting the weights on the 5,565 labelled closed cases is the obvious next step.
- **Case memory informs evidence but not the probability.** Retrieved prior cases are cited but carry no weight in the score. HHG-009 was closed as legitimate (known `Found` device, simulated step-up pass) although its card was linked to the HHG-005 ring. This was found after the final run and deliberately not re-tuned.
- **No LLM in the official path** (`tokens: 0`, honestly reported). Case summaries and SAR narratives are deterministic templates over graph facts, which guarantees every ID exists. The Phase-2 LangGraph + Gemini agent is not used for the official cases (§4).
- **No vector store / document RAG.** The README suggests loading the closed-case narratives, policy and regulatory PDFs into TigerGraph vector search. That wasn't done. Retrieval is structured graph retrieval (card, device, pattern + exposure band, before the case date), and the policy is encoded as rules.
- **Not all README patterns are exercised.** No case in the final run was classified `card_testing`, `out_of_region_use` or `account_takeover`. The detectors exist and are unit-tested on synthetic histories. Whether any case *should* have matched them is unknown without the key.
- **TigerGraph MCP** is not the transport in this path: tools call GSQL through pyTigerGraph's REST++ client.
- **Graph schema deviations** from the README's suggestion are documented in `graph_schema.py`: `H` type prefix; no `NEXT` edge (history is ordered by `ts` at query time); an added `InvestigationCase` vertex.

## 12. Reproducibility

```bash
cd backend
pip install -e ".[dev,benchmark]"
python -m app.benchmark.official fetch       # downloads the 5 official files (≈ 735 MB)
python -m app.benchmark.official prepare     # ~40 s
python -m app.benchmark.official load-graph  # ~10–15 min on Savanna; idempotent, resumable
python -m app.benchmark.runner --official    # validate + all 20 cases, ~3–5 min
pytest tests/unit/test_official_benchmark.py # 27 offline tests
```

This needs the same TigerGraph credentials as the app (`backend/.env`), and no LLM key. Without the official package, every command exits with `ERROR: OFFICIAL HHGOA DATASET UNAVAILABLE` and never falls back to the IEEE-CIS development data. A full run first deletes only the agent's own `InvestigationCase` vertices, so a replay can't "remember" cases from the future.
