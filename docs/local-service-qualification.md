# Local service qualification

The supported local image runs as numeric UID and GID `10001`, with one API worker and one thread per numerical library. The existing image build still trains the synthetic demonstration model and requires it to beat the seasonal naive baseline before saving it. Only the model directory and completed model file receive the serving identity's ownership. Runtime model access is read only.

The local monitoring Compose file publishes the API, Prometheus and Grafana on `127.0.0.1`. Its API has a read only root filesystem, all capabilities dropped, privilege escalation disabled, half a CPU, 512 MiB of memory and at most 64 processes or threads. The monitoring containers keep their existing storage and configuration. This local configuration does not modify the Kubernetes manifests or an active cluster.

`tools/qualify_service.py` checks a separately built or acquired exact trusted local image in an already authorized `desktop-linux` Docker environment. It does not build or download images, start Docker, select a global context, install services or change machine settings. Admission requires an existing accessible daemon, an available half CPU and 512 MiB budget, and permission for one ephemeral loopback API port. Run only within that scope.

## Identify source and image

The image argument must be its full local `sha256:` configuration ID. A tag alone is refused. This identifies local image contents. It is distinct from the published manifest digest used by `tools/container_origin.py`, which remains the existing release-origin verification path.

The consumer also requires a private source metadata JSON file:

```json
{
  "source_commit": "FULL_COMMITTED_SOURCE_SHA",
  "source_tree": "FULL_COMMITTED_TREE_SHA",
  "files": {
    "src/serve.py": "SHA256_OF_THE_ACTUAL_IMAGE_BUILD_INPUT",
    "src/train.py": "SHA256_OF_THE_ACTUAL_IMAGE_BUILD_INPUT",
    "src/features.py": "SHA256_OF_THE_ACTUAL_IMAGE_BUILD_INPUT",
    "src/provenance.py": "SHA256_OF_THE_ACTUAL_IMAGE_BUILD_INPUT",
    "requirements.txt": "SHA256_OF_THE_ACTUAL_IMAGE_BUILD_INPUT"
  }
}
```

Use actual lowercase digests in this file. All listed file digests are compared with the bytes inside the running image. Include other runtime source files when recording the build. Record the exact source acquisition and build separately, so supplying a commit string is not mistaken for establishing a trusted build origin. The consumer independently hashes the model bytes and canonical CSV observations and checks those identities against the health, window and prediction responses.

From the separately acquired source tree:

```text
python tools/qualify_service.py --context desktop-linux --image sha256:FULL_LOCAL_IMAGE_ID --source-metadata SOURCE_METADATA.json --output NEW_EVIDENCE_DIRECTORY
```

The destination must be new. An existing directory or file is refused before container operations, preserving earlier receipts. The command returns zero only after the declared qualification passes and owned cleanup succeeds. A failed attempt retains its completed samples, Docker command results and failure reason in `qualification.json`. It never rewrites an earlier attempt into a success.

## What the consumer checks

The main container receives the declared limits, a read only filesystem, all capabilities dropped, and `no-new-privileges`. Its sole published port binds to loopback. The consumer checks Docker's configuration and reads the actual UID, GID, effective capabilities and `NoNewPrivs` from process 1. It also reads `memory.max`, `cpu.max` and `pids.max` from the container's cgroup v2 interface. Missing or incompatible enforcement evidence refuses qualification. Required model and history reads must work, while an attempted write under `/tmp` must fail with a read only filesystem error.

Health and forecast-window responses must agree with independently calculated model and history identities. Both supported prediction boundaries must return the declared response types. Malformed months, year zero, a missing month and a month beyond the supported horizon must receive the supported refusal statuses. The metrics endpoint must expose request and prediction metrics.

The declared workload is finite:

| Phase | Requests | Clients |
|---|---:|---:|
| Warmup | 10 | 1 |
| Sequential | 100 | 1 |
| Concurrent | 200 | 2 |
| Overload | 10 bursts of 16 | 16 per burst |
| Shutdown observation | 100 | 2 |

Each HTTP request has a five second timeout. Every sample retains its status, response or error and latency. Summaries include all outcomes and preserve slow responses and failures. Warmup, sequential and requests from two clients must return valid supported predictions. Overload records what actually happens, including successful responses, service refusals and transport errors. It does not require or promise a particular number of `503` responses.

The consumer holds the final byte of one valid prediction request and verifies an accepted established API socket owned by process 1. It then requests a normal Docker stop while a bounded batch from two clients is active. After the application logs that shutdown has started, the consumer sends the final body byte and checks the completed prediction. Docker allows five seconds before forced termination. The receipt retains server acceptance evidence, the observed termination log, body completion time, response, exit state and elapsed stop time. Healthy restart uses the same exact image under the same settings and must return the same model/history identities and boundary prediction.

Four startup refusal cases follow. A temporary helper using the trusted exact image creates a malformed model file, a version of that trusted model with an invalid schema, and a gapped copy of the image's history. An empty owned directory supplies the missing model case. These fixtures are mounted read only into separate owned containers. They never alter the image, committed dataset, original model, ledger or historical study. Unknown external pickle files are not admitted or deserialized by this tool.

Container cleanup requires the unique ownership label created by this attempt. It cannot remove an unrelated container merely because a name was supplied. Uncertain startup still records the owned name before Docker is invoked, allowing later identity-checked cleanup. Cleanup failure remains a failed attempt. Generated refusal fixtures and evidence remain in the new output directory for inspection.

## Evidence limits

Unit tests verify admission, identity arithmetic, configured manifests, cleanup ownership, partial command evidence and preservation of errors. Those synthetic controls do not establish live containment or service behavior. The actual consumer receipt supplies that evidence for its exact image, workload and local environment.

The existing `tools/benchmark_serving.py` keeps the historical uncached handler from commit `56c23a8` and its original matched timing design. Its warmup compares the four shared prediction fields and checks the current model and history identities separately before timing. Original timing files and historical studies remain unchanged. Run that comparison only when a new timing claim requires it.

This qualification does not establish production scale, security certification, a Kubernetes experiment, or a real prospective forecasting service. Startup and recovery checks describe the retained synthetic demonstration artifact. Source delivery, existing deployment, local consumer qualification and a later forecasting product retain separate acceptance scopes.
