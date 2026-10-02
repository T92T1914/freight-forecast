# Container origin and inventory

The release workflow binds its build-origin and dependency-inventory
attestations to the exact published container manifest. A consumer check
requires this repository's release workflow, the selected version tag and
the full source commit, then checks an already downloaded image. It does
not start the container.

## Release contract

Only a `v*` tag starts publication. The workflow runs `make check` before
building and keeps the existing training gate inside the Dockerfile. It
publishes one `linux/amd64` image under the version tag and `latest`.
Consumers use the manifest digest rather than either mutable tag.

Buildx writes the published manifest digest to its metadata file.
`tools/container_origin.py record` rejects a missing digest, a descriptor
disagreement or an unexpected manifest type. The image configuration digest
is a different object and is not accepted as a substitute.

Syft inventories the registry image at that digest. Its SPDX 2.3 document
includes the detected operating-system and Python packages in the image,
rather than only the direct entries in `requirements.txt`. The Syft version
and release-archive SHA256 are explicit in the workflow. The archive is
verified before extracting the tool, without running a mutable installer.
Inventory generation does not perform vulnerability analysis and may miss
components that the catalogers do not recognize.

The pinned `actions/attest` action signs both build origin and the SPDX
inventory for the same subject name and digest. Both are published to GHCR.
Attestation permissions belong only to the tag-publication job. The workflow
does not submit dependency snapshots or attach release assets. Organization
storage records are disabled. No release is created by these additions.

After signing, the workflow pulls the digest and runs the same consumer
verifier described below. It retains `image.json`, `sbom.spdx.json` and
`consumer.json` as a seven-day workflow artifact after verification succeeds.
The verified inventory is also embedded in `consumer.json`. Attestations in
the registry remain the evidence source after the workflow artifact expires.

The image is pushed before it can be inventoried and attested. A later
failure does not retract that push or restore the previous `latest` tag.
Consumers must require successful verification for the chosen digest. A
published tag or an incomplete release run is not an accepted origin check.

## Verify a release before using it

Choose the manifest digest, full source commit and version tag from the
intended release. Review that commit and workflow independently. An
unsigned `image.json` is a convenient locator, not an independent trust root.
Use Docker and a GitHub CLI version supporting `gh attestation verify`,
`--bundle-from-oci`, `--source-digest` and `--source-ref`. The local preparation
checked these flags in GitHub CLI 2.96.0. No tool is installed by the verifier.

Authenticate with GHCR and GitHub through their normal supported interfaces
if required. Do not place credentials in the command or the receipt.
Replace the three placeholders with the chosen release identities:

```sh
docker pull --platform linux/amd64 ghcr.io/t92t1914/freight-forecast@sha256:<manifest-digest>
python tools/container_origin.py verify \
  --digest sha256:<manifest-digest> \
  --commit <full-source-commit> --tag <version-tag> \
  --output consumer.json
```

The helper checks the local image's exact repository digest, platform and
source labels. It then asks GitHub CLI to verify both attestation types from
GHCR with all of these constraints:

* Repository `T92T1914/freight-forecast`.
* Signer workflow `T92T1914/freight-forecast/.github/workflows/release.yml`.
* Exact certificate identity for that workflow at `refs/tags/<version-tag>`.
* GitHub's Actions OIDC issuer and a GitHub-hosted runner.
* The selected source tag and full commit.
* The exact container digest, SLSA provenance type and SPDX document type.

GitHub CLI performs the signature and certificate checks. The helper rejects
a nonzero CLI result, malformed output, a different subject, a missing SPDX
inventory or conflicting verified inventories. It never accepts arbitrary
saved JSON as cryptographic proof. It refuses to overwrite an existing
output and only writes a success receipt after every required check passes.

If you also download `sbom.spdx.json` from the workflow artifact, add
`--sbom sbom.spdx.json`. The helper compares that parsed document with the
verified signed inventory. The release workflow requires this comparison
before uploading its evidence, so an unrelated SPDX file cannot accompany
a successful consumer receipt.

Each external command has a two-minute timeout. JSON parsing rejects duplicate
keys and non-finite values, and accepts at most 64 MiB. Command output is
temporarily spooled to disk before that parsing limit is checked. The limit
does not cap an external command's disk output. Registry and CLI
failures remain failures. The helper does not retry, pull images, start
containers, log credentials or change account settings.

## What is verified and what remains open

The bounded local fixtures exercise the consumer call path, exact identity
arguments, downloaded-image checks and rejection behavior with inert tool
outputs. They are not signatures, real registry attestations or a published
release. The producer and real consumer path require the next separately
authorized version-tag release. Existing releases are not republished and
are not retrospectively described as attested.

A valid attestation establishes that the identified workflow made the signed
claim about that digest. It does not establish that the workflow was free of
compromise, every dependency is safe, the build is byte-reproducible, or the
model is accurate. The mutable base-image tag and training environment remain
separate reproducibility considerations. Source review and the existing
checks still matter.

The synthetic training gate does not replace the retained real-data study.
That study favored carrying forward the last observation. The prospective
ledger still has zero eligible issuances. None of the origin checks changes
those results or turns historical predictions into future forecasts.

## Tool contracts

* [GitHub CLI verification](https://cli.github.com/manual/gh_attestation_verify)
  documents the certificate, source and predicate constraints.
* [GitHub's attestation action](https://github.com/actions/attest) documents
  build-origin and SPDX modes, registry publication and storage-record scope.
* [Buildx metadata](https://docs.docker.com/reference/cli/docker/buildx/build/#metadata-file)
  distinguishes the published manifest from its configuration digest.
* [Syft's release](https://github.com/anchore/syft/releases/tag/v1.54.0) supplies
  the pinned Linux archive. [Syft](https://github.com/anchore/syft) documents
  image inventory and SPDX output.
