# Follow a forecast back to its model

The API returns `model_id` and `history_id` from `/health`, `/forecast-window` and `/predict`. The first is the SHA-256 of the exact model file loaded at startup. The second identifies the loaded monthly observations after sorting by date and normalizing volumes to numbers. Reordering CSV rows does not change that identity.

The fitted estimator, feature schema, original observation snapshot, chronological evaluation rows, acceptance rule, training cutoff, implementation hashes and dependency versions live in one joblib file. Publication still replaces that one file atomically. A separate metadata write cannot leave readers with a model from one run and evidence from another.

Startup validates the evidence before reporting readiness. It checks feature compatibility, the recorded evaluation against its observations and metrics, and the overlap between original and serving history. Changing an existing recorded observation requires retraining with that correction. Missing provenance requires explicit retraining as well. The service does not guess the origin of an older file.

Appending observations is supported without claiming that the model was refitted. Trimming older observations remains supported with at least 12 contiguous months and some overlap with the recorded history. A completely disjoint history needs a newly reviewed artifact. These checks establish consistency within this project, not that observations came from a particular organization.

The model and history are snapshots for the application's lifetime. Replacement affects the next startup. Reading and hashing the same model bytes prevents a concurrent replacement from labeling the old loaded model with the new file's identity. Dependency versions record the producing environment. They are not a promise of compatibility with every future library release.

## Reproduce the saved demonstration

```sh
python -m src.train
python -m src.export_evidence --output docs/visual-example-data.json
python tools/build_site.py
```

The exporter checks the saved model's predictions against its recorded evaluation before producing the demonstration data. The site builder rejects values detached from that evidence. The raw model file remains local and is not published with Pages. Rebuilding on another environment may produce different serialized bytes, so compare the recorded versions, implementation hashes and numerical results as well as the model identity.

The committed example uses seeded synthetic shipments. Other input is labeled as provided observations with unclassified origin. The displayed evaluation uses actual earlier observations as lag inputs. It is not a 24-month forecast issued in advance, and the public page does not call a live forecasting API. The separate rolling-refit experiment and its less favorable results remain separate evidence.

Only load trusted local joblib artifacts. Hashes identify bytes and help detect inconsistent records. They do not authenticate an author, make deserialization safe, or prove operational accuracy. The startup probe checks that the fitted estimator can produce a valid forecast. Reproducing the complete stored evaluation is a separate export check.
