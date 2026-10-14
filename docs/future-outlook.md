# A future month from the published BTS vintage

Protocol: `bts-freight-tsi-h3-last-observation-v1`, declared October 14, 2026
before creating a real record under this policy.

The product is a national monthly Freight Transportation Services Index outlook.
It carries forward the last observation in an inspected official snapshot. It
adds no fitted model, uncertainty method or operational decision rule. The
existing latest vintage study remains a separate retrospective comparison in
which last observation beat the fitted model. Its final period is already
inspected and will not be reused to select this policy.

## Target and information available at issuance

The target is exactly three calendar months after the snapshot's
`available_through` month. This is an observation horizon, not a claim of three
months of advance calendar notice. A real issuance uses the actual current UTC
clock and must precede the beginning of its target month. A current or elapsed
target is refused under this product. The original one step adapter retains its
separate nowcast and replay behavior.

The [BTS schedule](https://www.bts.gov/newsroom/transportation-services-index-release-schedule)
normally places a month's release in the second following calendar month. Under
that pattern, a published August snapshot acquired during October supports a
November target. A schedule does not prove release or source availability.
Missing or late data can prevent this product from being issued.

Retain the exact input CSV bytes, manifest and receipt. The validated bytes are
the bytes parsed for the policy. Their raw SHA256, canonical history identity,
available through month, source URL and retrieval clock must match the receipt.
The publication basis can be either an inspected exact publication receipt or a
successful official HTTP capture establishing that these bytes were available
by capture completion. Availability alone retains `published_at: null` and
`publication_verified: false`. It does not establish the first publication time
or that every old value was available in its present form years earlier.

## Point policy, comparator and cost

The point prediction and last observation baseline both equal the latest value
in that retained vintage. The seasonal comparator is the value for the actual
target month one year earlier, selected by calendar month from the same vintage.
An absent comparator refuses record construction. Intermediate future months
are never invented or fed back as observations.

Values are seasonally adjusted index points, with the 2000 average equal to 100.
The primary loss is absolute error in index points, assigning the same cost to
equal overprediction and underprediction. The ledger reports MAE only for
observed matching targets. There is no monetary cost estimate, carrier capacity
recommendation, local staffing decision or shipment count interpretation.

The policy family is `last_observation_h3`, its feature contract is
`bts-last-observation-h3-v1`, and its declared horizon is `3`. Its model identity
describes this deterministic rule. The complete policy content has a separate
digest. Old one step identities and records remain unchanged. The two horizons
have different policy identities and cannot be pooled into one policy cohort.

## Outcomes and finite acceptance

The default outcome is the target's inspected first release, published strictly
after issuance. An availability capture does not prove that publication order
and cannot replace an outcome publication receipt. Later vintages are separate
observations with their own predecessor links. The
[BTS revision policy](https://www.bts.gov/browse-statistical-products-and-data/transportation-economic-trends/revision-policy-tsi)
explains why revised historical values are not automatically first releases.
Unknown outcomes remain unobserved. Revisions are not additional forecasts.

Engineering acceptance requires independent calendar and year rollover checks,
the correct prior year comparator, future target refusal, source and policy
identity joins, complete receipt clock ordering, original one step compatibility,
exact and conflicting retries, restart/readback and existing report support.
The report displays both the available through month and observation horizon.
Tests use separately labeled fixtures and do not create real forecast evidence.

A real record may be appended only after the source, receipt and current target
pass those checks. Before outcomes arrive, the finite result is a qualified
issuance path and, when eligible, an unscored retained prediction. It establishes
no prospective accuracy or production service. There is no promotion threshold,
historical tuning, automatic polling, new registry or dashboard. A later losing
result remains evidence. Future releases and enough prospective observations
are external dependencies.

The original CSV, source manifest, evaluation protocol, numerical source fixture,
saved predictions, metrics and interval results remain unchanged. New captures
and local ledger records use separate paths and identities.
