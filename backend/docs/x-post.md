# X / LinkedIn post drafts

Replace `<BLOG_URL>` once the blog is published; the demo and GitHub links are filled in.
Every number below comes from `backend/benchmark/results/official/summary.json`.

## Main X post (266 characters with both links, which X counts as 23 each)

> Built an agentic fraud investigator on @TigerGraphDB for #HHGoa26: it traces devices across cards, says how sure it is, asks for evidence when unsure, and acts under bank policy. 20/20 official cases done.
>
> Blog: <BLOG_URL>
> Demo: https://youtu.be/0dqjXyY2Hoc

## Thread (optional follow-ups)

**2/**
> A risk score isn't a verdict. The dataset says most alerts above 0.7 are legitimate.
> So the agent gathers evidence first: card history, device neighbourhood, closed cases, its own earlier cases. Then it picks a next-best action and an approval route (auto / L1 / L2).

**3/**
> The graph moment: one alert's device led, two hops away, to 27 other customers' cards in a month. It was the same device as the bank's closed ring cases. Case + SAR filed, connected cards under monitoring.

**4/**
> Our first run was wrong: it called popular phones "fraud rings". The fix came from the data: the real ring device is marked New on 100% of its transactions and averages about 2 per card. Both wrong runs are kept in the repo.

**5/**
> Honest numbers: 20/20 cases answered in the official format, 20/20 persisted in TigerGraph and verified, 12 recommendations updated after new evidence. Accuracy: unknown until TigerGraph scores it, because the package has no answer key.
> Code: https://github.com/rohan911438/HHGOA_26

## LinkedIn version

> For Hacker House Goa '26 we built an agentic fraud investigation system on TigerGraph (@TigerGraphDB).
>
> Instead of asking an LLM "is this fraud?", the agent investigates. It traverses the graph from a flagged transaction to the card's history, the device's other cards and the bank's closed cases. It turns what it finds into named evidence and tracks how many independent lines of evidence it has. It recommends a next-best action with the approval route the bank's policy requires, before and after asking the customer.
>
> On the official 20-case benchmark, every case produced an answer in the official format and an investigation case written to TigerGraph and read back. 12 recommendations changed as new evidence arrived, and 5 suspicious activity reports were filed where the policy requires them. One case was only solvable by looking at other customers' cards: a single device, 27 victims, in one month.
>
> We don't know our accuracy yet (the answer key is private), and we wrote up what we got wrong along the way.
>
> Blog: <BLOG_URL> · Demo: https://youtu.be/0dqjXyY2Hoc · Code: https://github.com/rohan911438/HHGOA_26
>
> #HHGoa26 #TigerGraph #GraphDatabases #FraudDetection #AIAgents
