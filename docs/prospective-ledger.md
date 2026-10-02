# Retain a prediction before evaluating it

The local ledger keeps a prediction, the information available when it was
issued, and subsequent outcome vintages as separate records. An unfavorable
result remains evidence. Retaining it does not depend on beating a baseline.
The serving artifact's existing `model_mae < naive_mae` promotion rule is
unchanged.

The [public reading view](https://t92t1914.github.io/freight-forecast/ledger.html)
separates real issuances, retrospective replays and synthetic fixtures. Its
saved example contains no real forecast. The small invented fixture exercises
a first release, a revision, a missing release and an explicit correction.
The separately labeled BTS replay uses the retained July 2026 input vintage.
It is not a prediction issued before August began.

## Record and identity boundaries

`src.ledger.Ledger` uses a local SQLite database. Each append contains a caller
idempotency key, a content identity from the existing `src.provenance.digest`,
and the actual local recording clock. The first issuance identity remains the
reference for observations and corrections. Retrying the same key and contents
returns the original event. Reusing a key for different contents fails.

An issuance records the series, units, target month, product classification,
issue timestamp with offset and timezone convention, source publication and
retrieval clocks, available-through month, exact snapshot byte hash and
canonical history identity. It also retains model, feature and policy
identities, training cutoff, horizon, prediction and both baselines. Hashes
identify content. They do not authenticate the author or establish an
independent issuance timestamp.

The BTS adapter uses the existing snapshot validator. It rejects modified
bytes, incompatible metadata and an incomplete calendar before constructing
the record. Its byte identity comes from the manifest verified against the
same buffer used to parse the history. Replacing or removing the file after
that read does not change the captured vintage. This does not preserve a copy
of the source file or authenticate its publication.
BTS values are index points with the 2000 average equal to 100.
They are not synthetic shipment counts.

The adapter's declared point policy carries forward the last observed month.
Its seasonal comparator uses the same month a year earlier. This small policy
requires no new fitted model and supplies no uncertainty interval. Only a
single immediately subsequent target is supported. A model that happens to
have a hash is not permission to invent a multi-step feature contract.

`real_issuance` requires a verified source publication receipt and a current
issuance clock. Backdating more than five minutes is rejected. A target whose
month has begun is an `elapsed_period_nowcast`, not a future forecast. A
`retrospective_replay` has its own classification and cannot enter the real
issuance cohort. The implementation checks supplied evidence, not the truth
of a caller's verification label. Inspect the original publication before
setting that label.

## Publication and the current BTS gap

The retained [snapshot manifest](../data/public/bts-freight-tsi-2026-09-24.json)
covers January 2000 through July 2026. Its retrieval clock and dataset update
clock do not establish the exact publication time of every value in that
vintage. The [BTS release schedule](https://www.bts.gov/newsroom/transportation-services-index-release-schedule)
lists the August release for October 14, 2026. A schedule is not proof that
a release occurred or that particular bytes were available.

On September 30, July's immediately subsequent target is August, an elapsed
period. The one-step adapter therefore cannot use this snapshot to produce a
genuine future September or October forecast. No real issuance was created
to fill the public table. A current, inspected source publication and a
compatible immediately subsequent target are separate requirements for a
future issuance. No polling service or schedule is installed.

The [historical BTS evaluation](real-data-evaluation.md) remains unchanged.
Last observation beat the fitted model on that latest-vintage retrospective
comparison. The [BTS revision policy](https://www.bts.gov/browse-statistical-products-and-data/transportation-economic-trends/revision-policy-tsi)
also explains why today's revised historical values cannot be relabeled as
past first releases.

## Local workflow

Use the existing development environment from the repository root. These
commands do not train, publish a model or modify the serving artifacts.

```sh
python tools/ledger_fixture.py --db reports/ledger-demo.sqlite --output reports/ledger-demo.json
python -m src.ledger --db reports/ledger-demo.sqlite export --output reports/ledger-latest.json --outcome-policy latest_available
python -m src.ledger_bts --data data/public/bts-freight-tsi-2026-09-24.csv --manifest data/public/bts-freight-tsi-2026-09-24.json --kind retrospective_replay --db reports/bts-ledger.sqlite --key july-vintage-replay
python -m src.ledger --db reports/bts-ledger.sqlite export --output reports/bts-ledger.json
```

To submit a reviewed record rather than a generated fixture:

```sh
python -m src.ledger --db reports/local-ledger.sqlite issuance --key issuance-001 --record issuance.json
python -m src.ledger --db reports/local-ledger.sqlite observation --key release-001 --record observation.json
python -m src.ledger --db reports/local-ledger.sqlite correction --key correction-001 --record correction.json
```

The [saved example](ledger-fixture.json) supplies complete field shapes. Its
values and publication clocks are visibly synthetic. They are not source
receipts to reuse for a real issuance. For a real BTS record, the adapter also
accepts `--publication-record` containing `published_at`,
`publication_verified` and `publication_basis`. Only pass inspected evidence.

An observation matches the original issuance's target, series and units.
`first_release`, `revision`, `latest_snapshot` and `unavailable` remain
different states. A revision references its observed predecessor. An
unavailable first release has no value or invented hash. A correction names
the event being excluded and its reason. It leaves that event intact. Append
a separately identified replacement when needed.

For a transcribed first release, exclude the incorrect observation explicitly,
then append a new `first_release` observation whose `supersedes_event_id`
references that excluded record. A second uncorrected first release is
rejected. The original value and the correction both remain in the history.

## Transaction and evaluation behavior

The database uses a rollback journal, `synchronous=FULL` and an explicit
`BEGIN IMMEDIATE` transaction. SQLite serializes writers. Each connection
waits at most ten seconds for its lock, then fails visibly. An interrupted
transaction rolls back without exposing a partial event. Updates and
deletions through ordinary SQL are rejected by triggers. This is application
retention, not a security boundary against an owner who can edit the database
or remove those triggers. SQLite's [atomic commit explanation](https://www.sqlite.org/atomiccommit.html)
describes its filesystem and hardware assumptions.

Records are limited to 64 KiB. Connections close after each operation.
Keep the database on a supported local filesystem and back it up through
SQLite's backup interface while active, or copy it only when closed. Do not
copy a live database without its journal or put it on an unreliable shared
filesystem and infer durability from passing local tests.

Derived JSON and HTML are rebuilt from the event history.

Readback checks each event's content identity and its link to an earlier
original issuance before deriving a view. Observation targets, revision
predecessors and correction targets must agree with that issuance. An
inconsistent restored or modified link fails visibly rather than attaching
an outcome to another prediction. These checks do not authenticate the
database or repair its records.

The default outcome policy is `first_release`. `latest_available` is an explicitly different
evaluation. It selects the latest publication clock, with append sequence as
the tie break. An unknown first release remains unobserved in the default
view even when a latest snapshot exists.

Within each series, units, target, product and policy cohort, the earliest
non-excluded issuance is selected. Later issuances remain visible as repeats.
Outcome revisions never add an independent forecast. Metrics remain separate
by real, replay and fixture evidence, product classification and policy.
Every view includes target coverage, scored counts, missing outcomes,
excluded records and repeat counts. A correction can change evaluation, so
inspect the retained correction history alongside any new metric.

```sh
python -m pytest -q tests/test_ledger.py tests/test_ledger_bts.py tests/test_ledger_report.py
python tools/build_site.py
```

The checks exercise concurrency, conflicting retries, interruption and
restart, revised and delayed outcomes, incompatible artifacts, invalid
values, failed reconciliation and deterministic view recovery. The process
interruption fixture exits an owned helper during an uncommitted transaction.
It does not simulate a power failure or prove every storage device safe.
