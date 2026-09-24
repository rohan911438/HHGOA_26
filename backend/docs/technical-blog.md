# Building an Agentic Fraud Investigation System with TigerGraph

*Hacker House Goa '26, TigerGraph challenge. Team BROTHERHOOD.*

> Links: GitHub: `<GITHUB_URL>` · Demo video: `<DEMO_VIDEO_URL>` · Live dashboard: https://hhgoa-fraud-frontend.vercel.app

## The problem

A fraud alert is not an answer. A bank's model scores a transaction at 0.79 and an analyst now has to work out what happened. That means pulling the card's history, checking whether the device is new, looking at where the card is normally used, finding out whether this device has touched other customers' cards, and reading old cases that look similar. Then they apply the bank's policy and decide what to do, who has to approve it, and whether a regulator needs a report.

The challenge dataset makes the point bluntly. Every one of the ~590,000 transactions carries the bank's risk score, and the README warns: *"Above 0.7, most flagged transactions turn out to be legitimate. Some fraud scores near zero."* Half of the 20 benchmark cases are legitimate. An agent that blocks every alert scores badly.

So the task isn't classification. It's investigation: gathering evidence, knowing how sure you are, asking for more when it's needed, and acting under a policy.

## Why fraud investigation needs a graph

Fraud is relational. Taken alone, a $74.96 online purchase is unremarkable. It becomes interesting when you follow its device profile out to every other card that device touched in the last month.

That is exactly what happened on benchmark case **HHG-014**. An analyst wrote: *"several cards this month show purchases from the same unusual device profile."* The agent went from the flagged transaction to its `HDeviceProfile` vertex and back out through `H_FROM_DEVICE` and `H_MADE` to the cards. It found the same Samsung SM-G935F / Chrome for Android profile, behind an anonymous proxy, on **27 other customers' cards** within 30 days. It is the same device that appears in four of the bank's closed ring cases. That's a two-hop traversal in GSQL, and in a relational schema it's a self-join nobody writes at 2 a.m.

The graph also has to answer the opposite question. A device profile shared by hundreds of cards is usually not a ring: it's a popular phone. More on that below, because it was our biggest mistake.

## Architecture

```
case_pack.csv row (trigger)
        ↓
Investigator  ── 12 recorded steps ──────────────────────────────┐
        ↓                                                         │
Controlled graph tools (fixed GSQL, counted, retried)            │
        ↓                                                         │
TigerGraph  HHGOA_IEEE                                            │
   HCustomer–HCard–HTxn–HDeviceProfile / HEmailDomain /           │
   HBillingRegion, HClosedCase, InvestigationCase                 │
        ↓                                                         │
Signals → Uncertainty → Memory → Fraud Policy v1.0 → Case + SAR ──┘
        ↓
cases/HHG-0NN.json  +  InvestigationCase written to graph, read back
```

The project has two layers:

- **The Phase-2 investigation platform:** a FastAPI backend, a LangGraph orchestrator with a Gemini-backed LLM, an evidence model, an uncertainty engine, a case manager, and a Next.js analyst dashboard. It was built and deployed while the official data was not yet available, on the public IEEE-CIS data.
- **The official benchmark pipeline** (`backend/app/benchmark/official/`): built on the official HHGOA_IEEE package in its own TigerGraph graph, speaking the official policy's exact vocabulary. It reuses the platform's TigerGraph client, credentials and retry discipline, and keeps the same principle: *facts come from the graph, decisions come from rules, and every step is traceable.*

## TigerGraph investigation layer

The official package has 590,742 transactions, 144,432 identity records, 5,565 closed investigations and 20 cases. We load it into a graph close to the README's suggested schema: `HCustomer -OWNS- HCard -MADE- HTxn`, and `HTxn` linked to `HDeviceProfile`, `HEmailDomain` (purchaser and recipient) and `HBillingRegion`. Closed cases link to their transactions and cards. We added one vertex type the README asks for implicitly: `InvestigationCase`, the agent's own output.

Two practical lessons from loading 590k transactions into Savanna:

1. **Use loading jobs, not REST upserts.** A GSQL `LOADING JOB` fed with posted tab-separated chunks loaded everything in about 15 minutes. Two quirks we measured: `HEADER="true"` is not honoured for posted data (the header row became a vertex called `card_id`), and `proxy` is a reserved word.
2. **Make the load resumable.** Two SSL errors hit mid-load. Upserts are idempotent, so the loader retries a chunk and can resume from an offset.

One detail the README doesn't spell out is how card IDs like `C12382-K1` are derived. We measured it rather than guessing. Ranking a customer's card types (`card6`) alphabetically reproduces all **14,975** labelled transaction→card pairs in the package. The loader re-checks this on every run.

## Controlled investigation tools

The agent can't write GSQL. It gets eight tools, each a fixed interpreted query with typed parameters:

| Tool | Question it answers |
|---|---|
| `get_transaction` | What exactly was flagged? |
| `card_history` | What does normal look like for this card? |
| `customer_cards` | What else does this customer hold? |
| `device_neighbours` | Who else used this device, when, how often, how many cards all-time, what share marked `New`, and which confirmed-fraud closed cases touch it? |
| `closed_cases_for_cards` | Has this customer been here before? |
| `similar_closed_cases` | Which closed cases share this pattern and exposure band, *closed before this case opened*? |
| `case_memory` | Which of the agent's own earlier cases share this card or device? |
| `write_case` / `read_case` | Persist the case, then prove it persisted. |

Every call is counted (8–9 per case, 173 in total) and logged with its latency in a per-case `trace.json`.

## From graph facts to evidence

The tools return facts. A pure-function layer turns them into named signals, each carrying a human-readable detail and the entity IDs it rests on. Each threshold comes from the dataset's README or from the bank's closed-case notes, and the source is cited in the code:

- **Card testing:** 3+ online authorizations under $5 within an hour, then a larger purchase (pattern 1, R5).
- **Card-not-present burst:** 2–4 unusual online purchases within 48 hours (pattern 2).
- **New device** (identity record `New`), **known device** (`Found`, or seen before on the card), and **proxy** (`ANONYMOUS`/`HIDDEN`; `TRANSPARENT` doesn't count).
- **Out-of-region:** card-present use in a region not in the card's history while home-region activity continues. Several days in one new region is a trip, not a clone (pattern 4).
- **Account-takeover markers:** match-flag anomalies, purchaser email change, mixed channels.
- **Recurring charge:** same product and amount at monthly spacing (R7).
- **Two undocumented schemes** the closed-case notes describe: a **shared-device ring**, and **structuring** (four online purchases just under $500 inside 40 minutes).

## The uncertainty model

Each alert gets a fraud probability: a trigger-specific prior plus log-odds contributions from the signals, with the bank's risk score as a deliberately small input. More important than the number is the **count of independent lines of evidence**: sequence, amount, device, network, credentials, region. Policy §6 says to stop only when the probability is ≥ 0.85 or ≤ 0.15 *and* at least two independent pieces of evidence support it. R1 says never block on a single signal below 0.70. The agent tracks both.

The weights are hand-set from the README's language ("stronger than pattern 2, still not proof"), not fitted. We say so plainly below.

## GraphRAG and case memory

Retrieval here is structured graph retrieval, not vector search. For each case the agent pulls:

- the customer's own closed cases;
- closed cases of the same pattern in a similar exposure band that were closed **before this case opened** (no peeking at the future);
- confirmed-fraud cases touching the same device;
- the agent's own earlier `InvestigationCase` vertices, linked by card or device.

The cases run in `opened_at` order, so memory grows as the benchmark proceeds. In the final run it produced exactly one hit, and an instructive one. **HHG-009** (28 Dec, a customer disputing a $30.02 online purchase) retrieved **CASE-HHG-005** (8 Dec), because HHG-009's card was one of the cards the agent had put under monitoring as linked to HHG-005's device ring. Every retrieved ID is cited in `similar_prior_cases` or the evidence list, and each one is checked to exist in the dataset.

The honest caveat: memory is retrieved and *cited*, but it doesn't yet move the probability. HHG-009 was closed as legitimate on the strength of a known (`Found`) device, even though its own case file notes the link to a ring case. We found this after the final run and chose to report it rather than re-tune the scoring after seeing the outputs.

## Agent orchestration

The investigator runs the challenge's core flow as 12 recorded steps: trigger → open case → history → device neighbourhood → memory → assess → initial next-best action → evidence request → final next-best action → SAR decision → write to graph → read back. `asked_after_step` in each evidence request points at a real step number in `trace.json`.

Customer and analyst replies are not provided (README §5), so the agent simulates them with **one stated rule**: the assumed reply is the one its own probability favours. It's written into every `evidence_requests[].assumed_response`.

## Next-best action

The policy module encodes Fraud Policy v1.0 with its exact identifiers: 14 actions, rules R1–R10, and one function that assigns every approval route (`auto`, `L1` for declines and blocks up to $2,500, `L2` for larger blocks, `BLOCK_ALL_CARDS` and every report). The agent recommends; only `auto` actions could be executed, and `L1`/`L2` wait for a human.

A typical uncertain case, **HHG-013**: the model scores a $35.66 online purchase at 0.76. The graph shows a device marked `New` for this account, but the amount and product are normal for the card. Probability 0.60 on one line of evidence, so the initial action under R1 is `VERIFY_WITH_CUSTOMER` + `CREATE_CASE`. The simulated reply is a denial, so the final action is `BLOCK_CARD` (`L1`) + `CREATE_CASE` with pattern `card_not_present_new_device`. `what_changed` records the move from 0.60 to 0.87.

## Case management and SARs

Each case becomes an internal record: status, verdict, probability, pattern, affected transactions, first suspicious transaction, connected cards and devices, exposure, a typed evidence list, and the prior cases used. It's written to TigerGraph as an `InvestigationCase` vertex with six edge types, then **read back and compared field by field**. `written_to_graph: true` is only set on a match.

Policy §3a separates a case (internal) from a suspicious activity report (regulatory). The agent files a SAR only for fraud that is confirmed or strongly suspected **and** above $1,000, linked to a shared device, or coordinated/undocumented. The narrative follows FinCEN's who/what/when/where/how/why structure, is assembled only from graph facts, and every subject ID is verified.

## The official 20-case benchmark

We ran all 20 official cases with one command: `python -m app.benchmark.runner --official`.

| | |
|---|---|
| Cases executed | 20 / 20 |
| Answer files in the official format | 20 / 20 |
| Written to TigerGraph and read back | 20 / 20 |
| Pass all format and policy conformance checks (35–39 each) | 20 / 20 |
| NBA + route before and after evidence | 20 / 20 |
| Evidence requested / recommendation changed after it | 12 / 12 |
| SARs filed | 5 |
| Verdicts | 12 fraud · 6 legitimate · 2 uncertain |
| Median latency / graph calls per case | 6.4 s / 9 |

**What we can't tell you is accuracy.** The package ships no answer key: *"We score them against an answer key you don't have."* So there's no pattern-accuracy or NBA-accuracy figure here, and we haven't invented one. The conformance checks prove the answers are well-formed and follow the policy mechanically. For example, every route matches §2, `sar.file` agrees with `FILE_REPORT`, exposure equals the sum of the affected amounts, and every ID exists. They don't prove the answers are right.

## What worked

- **Traversal for the hard case.** HHG-014's shared device only shows up if you ask what happened on *other* cards, exactly as the README hints.
- **Refusing to guess.** Two customer reports (HHG-003, HHG-018) are card-present purchases where the graph shows nothing either way. The agent marks them `uncertain`, blocks under R2 and escalates under R8, rather than flipping a coin.
- **Measuring instead of assuming:** the card-ID rule, the popularity of device profiles, and the ring fingerprint.

## Failure analysis: our first run was wrong

The first full run called **16 of 20 cases fraud** and filed 10 SARs, mostly for an "undocumented device ring". The README says half the cases are legitimate, so we went back to the data.

The ring rule, "≥ 2 other customers on this device profile within 30 days", was matching phones everyone owns. Across 9,705 device profiles, the median profile has 1 card, the 95th percentile has 22, and the 99th has 112. Our "rings" had 66 to 297 cards a month. The bank's own documented ring device looks nothing like that: it is marked `New` on **100%** of its transactions, sits behind an anonymous proxy on **100%**, and averages **2.2 transactions per card**. A real person's phone becomes `Found` on the second purchase; a fraud device hops between cards. We rebuilt the rule on that fingerprint, fixed a proxy-parsing bug (`IP_PROXY:TRANSPARENT` was counted as suspicious), and stopped linking cases through mass-market devices.

The second run exposed a subtler issue: five answers said `fraud` with pattern `none`. Two causes: we ignored the identity record's `Found` flag, and we had no rule for "the customer denies it and the graph has nothing to add". Both are fixed, and both earlier runs are archived in the repository with their summaries.

We changed logic after looking at our own outputs. There was no answer key to fit to, and every change is a data-grounded bug fix, but the final numbers are not from a blind first attempt. We'd rather say that than hide it.

## Security and human approval

- The frontend never talks to TigerGraph. The agent has no free-form GSQL; its only graph write is its own case vertex.
- `L1`/`L2` actions are recommendations that wait for a human. Nothing is executed.
- Credentials live in `.env` (gitignored), and the benchmark artifacts contain no secrets.
- The official pipeline never reads the Kaggle files. The README calls recovering outcomes from them disqualifying, and the loader only reads the official package.
- No chain of thought is exposed: explanations are evidence, rules and stated assumptions.

## Lessons learned

1. **Popularity isn't suspicion.** Any "shared entity" signal needs a baseline for how shared that entity normally is.
2. **Absence of evidence deserves its own verdict.** `uncertain` with escalation is a legitimate answer, and the policy was written to accept it.
3. **Write, then read back.** "Written to graph" is a claim; a read-back makes it a fact.
4. **Keep the wrong runs.** They're the most useful documentation we have.

## What we would improve with more time

- Make **case memory change the decision**, not just the evidence list: a card previously linked to a ring case should raise the prior (see HHG-009).
- **Calibrate** the probability weights on the 5,565 labelled closed cases instead of setting them by hand. Calibration is scored.
- Load the policy, closed-case narratives and the FinCEN/FATF documents into **TigerGraph vector search** for true document GraphRAG.
- Put the **LLM back in the loop** for case summaries and SAR narratives, validated against dataset IDs, and route the tools through **TigerGraph MCP**.
- Use richer graph algorithms (community detection over device/card bipartite graphs) to find rings without hand-built fingerprints.
- Monitor November–December on its own and investigate alerts beyond the 20 cases.

## Conclusion

The graph does the finding, the policy does the deciding, and the agent's job is to connect them honestly: gather the evidence, say how sure it is, ask when it isn't sure, and record every step. Every one of the 20 official cases is investigated, decided before and after evidence, written to TigerGraph and read back. We don't yet know how many we got right, and that's exactly what the answer key is for.

*Code: `<GITHUB_URL>` · Demo: `<DEMO_VIDEO_URL>` · Built on TigerGraph Savanna for Hacker House Goa '26. @TigerGraphDB*
