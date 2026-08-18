# Research modes

Use one versioned evidence workflow, then route to the validated analysis pack that matches the business question. Cross-department applications and handoff contracts are listed in [department-recipes.md](department-recipes.md).

## Competitor intelligence

**Question:** What does a verified competitor publish, what performs, what do customers ask, and where are the observable information gaps?

**Discovery unit:** verified company-owned accounts.

**Primary evidence:** `platform_census.csv`, `content.csv`, and selected public conversations in `comments.csv`.

**Analysis:** emergent content taxonomy, supply–performance gap, customer-demand frequency, response patterns, and opportunity hypotheses.

**Commands:**

```bash
python3 scripts/prepare_analysis.py --bundle runs/target
python3 scripts/apply_analysis.py --bundle runs/target
```

## Customer voice

**Question:** What are customers asking, reporting, requesting, or praising; how serious are the visible issues; and how does the organization visibly respond?

**Discovery unit:** public customer signals found within a declared set of accounts, queries, platforms, markets, and dates.

**Primary evidence:** captured public conversations in `comments.csv`, with parent content in `content.csv` for context. Account-owned comments may be retained as reply evidence but are excluded from customer-demand counts.

**Analysis:** corpus-derived issue taxonomy, intent, sentiment, severity, confidence, visible official-response coverage, and evidence-led escalation candidates.

**Commands:**

```bash
python3 scripts/prepare_customer_voice.py --bundle runs/target
python3 scripts/apply_customer_voice.py --bundle runs/target
```

Read [customer-voice-playbook.md](customer-voice-playbook.md) before completing the Agent task.

## Department applications

Channel intelligence, market-entry research, owned-channel monitoring, presales evidence, and product-feedback review can reuse the same ledger. Treat them as declared business recipes, not implemented analysis validators, until their discovery rules, analysis fields, validation, tests, and public-safe example have been completed.

Two reusable extension playbooks are available:

- [monitoring-playbook.md](monitoring-playbook.md) defines a material-change alert contract on top of stable-ID incremental merge;
- [persona-research.md](persona-research.md) defines evidence-grounded EPC, installer, channel, or end-customer roles without inventing an “average” persona.

They require their own declared source population, review rules, and output checks before being described as deployed business modes.

## Collaboration handoff

Competitor intelligence and customer voice are the implemented analysis modes. Their reviewed outputs may be handed off to a collaboration layer for decision tracking, but that handoff does not create a new validated research mode. See [collaboration-handoff.md](collaboration-handoff.md).

For any mode, record the target, market, time range, platforms, query/account boundary, cutoff, collection status, and limitations in `run_manifest.json`.
