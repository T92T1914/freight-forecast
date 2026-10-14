# Retain the source before issuing a record

The BTS capture command acquires the official projected Freight TSI CSV and its
dataset metadata into one new directory. It retains their response body bytes,
URLs, HTTP status, headers, retrieval clocks and hashes. It validates the captured
CSV through the existing snapshot loader before producing a compatible manifest
and a receipt of public availability.

```sh
python tools/capture_bts_snapshot.py --output reports/new-bts-capture
```

The output path must not exist. An existing directory, file or symbolic link is
refused before any request, so retrying a failed capture requires a new directory.
Original captures and their failures remain available for inspection. The command
does not replace repository data, fit a model, create a ledger issuance or install
a recurring service.

## Capture and admission

Only dataset `bw6n-ddqk`, its `obs_date` and `tsi_freight` projection, and its
metadata are supported. Requests and normal redirects must use the declared HTTPS
paths on the official `data.transportation.gov` or `data.bts.gov` portals. Different
hosts, datasets or query projections are refused. Capture is limited to 2 MiB per
response. An oversized response retains at most that bound plus one byte as its
overflow witness, with no admission receipt.

Each request has a 30 second socket timeout. Body reading also checks a 30 second
elapsed budget between bounded single reads. An already blocked read may finish
at its socket timeout after that budget expires. These bounds do not promise a
strict 30 second limit for the entire two request command.

A successful directory contains:

* `source.csv`, the unchanged official projected response body.
* `metadata.json`, the unchanged official dataset metadata response body.
* `manifest.json`, the compatible snapshot identity and coverage record.
* `availability.json`, the validated availability bound and identity joins.
* `http-receipts.json`, transport evidence for both requests.

The parser uses the exact captured buffer whose hash appears in the manifest and
availability receipt. It does not reread a potentially replaced source path to
identify the observations. Before admission, retained source and metadata bytes
must still match those captured buffers. Snapshot admission checks the declared
fields, exact byte hash, positive finite observations and complete monthly calendar. The
availability receipt joins that source hash, normalized history identity,
dataset, field and final observed month.

On HTTP failure, timeout, malformed source, conflicting metadata or failed
delivery, received source/metadata bodies remain in the new directory. A
`capture-failed.json` record preserves the transport evidence and error when the
filesystem permits it. No manifest or availability receipt is accepted from a
failed command. A disk or cleanup failure can prevent that failure record or its
cleanup from completing, and the original exception reports that limitation.
Inspect a failed directory rather than treating a partial body as a snapshot.

## What the clock means

The receipt has schema version 1 and kind `available_by_capture`. Its
`available_by` and `retrieved_at` are the same UTC completion clock for the
successful CSV response. `source_url` is the fixed request URL and
`resolved_source_url` records its admitted final URL. `sha256`, `history_id`,
`dataset_id`, `field`, `last_month` and `http_status: 200` bind the clock to the
captured observations. The compatible manifest uses that same `source_url` and
retrieval clock.

This proves that those input values were obtainable by the recorded bound. It
does not establish the exact first publication time. A later publication record
therefore keeps `published_at` null and `publication_verified` false when its
basis is only this availability capture. Dataset update timestamps, response
Last-Modified and a release schedule do not substitute for original publication
timing.

For issuance admission, the verified input availability bound must precede the
issuance clock. An outcome retrieved later may already have existed earlier, so
this receipt cannot establish that an outcome was first published after the
issuance. First release reconciliation still needs separately qualified source
and publication evidence. Retained latest vintage values remain latest vintage
values, including their revisions. They do not recreate historical first
releases or turn an elapsed target month into a future forecast.
