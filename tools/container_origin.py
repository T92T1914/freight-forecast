"""Bind a release record and consumer verification to one container digest.

GitHub CLI performs signature and certificate verification. This module adds
the project policy and checks an already downloaded image without running it.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY = "T92T1914/freight-forecast"
IMAGE = "ghcr.io/t92t1914/freight-forecast"
WORKFLOW = f"{REPOSITORY}/.github/workflows/release.yml"
PROVENANCE = "https://slsa.dev/provenance/v1"
SBOM = "https://spdx.dev/Document"
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_SUBPROCESS_SECONDS = 120


class OriginError(ValueError):
    """A required identity or verification result was not established."""


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OriginError("JSON contains a duplicate key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise OriginError("JSON contains a non-finite value")


def parse_json(raw):
    if len(raw) > MAX_JSON_BYTES:
        raise OriginError("JSON exceeds the verification size limit")
    try:
        return json.loads(
            raw, object_pairs_hook=_object_pairs, parse_constant=_invalid_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise OriginError("Malformed JSON verification result") from error


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise OriginError("Expected a lowercase SHA256 manifest digest")
    return value


def _release_identity(commit, tag):
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise OriginError("Expected a full lowercase source commit")
    if not isinstance(tag, str) or not re.fullmatch(r"v[A-Za-z0-9_.-]{1,127}", tag):
        raise OriginError("Expected a version tag usable as a container tag")


def release_record(metadata, commit, tag):
    """Select the published manifest digest, never the image config digest."""
    _release_identity(commit, tag)
    if not isinstance(metadata, dict):
        raise OriginError("Build metadata must be an object")
    digest = _digest(metadata.get("containerimage.digest"))
    descriptor = metadata.get("containerimage.descriptor")
    if not isinstance(descriptor, dict) or descriptor.get("digest") != digest:
        raise OriginError("Build descriptor and published digest disagree")
    if descriptor.get("mediaType") not in {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }:
        raise OriginError("Expected the single-platform release manifest")
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "workflow": ".github/workflows/release.yml",
        "source_commit": commit,
        "source_ref": f"refs/tags/{tag}",
        "image": IMAGE,
        "digest": digest,
        "reference": f"{IMAGE}@{digest}",
        "platform": "linux/amd64",
    }


def verification_command(digest, commit, tag, predicate_type):
    _digest(digest)
    _release_identity(commit, tag)
    if predicate_type not in {PROVENANCE, SBOM}:
        raise OriginError("Unsupported attestation predicate")
    return [
        "gh",
        "attestation",
        "verify",
        f"oci://{IMAGE}@{digest}",
        "--repo",
        REPOSITORY,
        "--signer-workflow",
        WORKFLOW,
        "--cert-identity",
        f"https://github.com/{WORKFLOW}@refs/tags/{tag}",
        "--cert-oidc-issuer",
        "https://token.actions.githubusercontent.com",
        "--source-ref",
        f"refs/tags/{tag}",
        "--source-digest",
        commit,
        "--deny-self-hosted-runners",
        "--predicate-type",
        predicate_type,
        "--bundle-from-oci",
        "--limit",
        "2",
        "--format",
        "json",
    ]


def _run_json(argv):
    # Use the supported tools and their normal credential storage. Never log
    # stderr, tokens or environment values, and never invoke a command shell.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
                timeout=MAX_SUBPROCESS_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OriginError(f"{argv[0]} could not complete verification") from error
        if result.returncode != 0:
            raise OriginError(f"{argv[0]} verification failed ({result.returncode})")
        output.seek(0)
        return parse_json(output.read(MAX_JSON_BYTES + 1))


def check_local_image(document, digest, commit, tag):
    """Check the downloaded manifest reference and the selected platform."""
    _digest(digest)
    _release_identity(commit, tag)
    if not isinstance(document, list) or len(document) != 1:
        raise OriginError("Expected exactly one downloaded image")
    image = document[0]
    if not isinstance(image, dict):
        raise OriginError("Malformed downloaded image")
    repo_digests = image.get("RepoDigests")
    if (
        not isinstance(repo_digests, list)
        or any(not isinstance(value, str) for value in repo_digests)
        or f"{IMAGE}@{digest}" not in repo_digests
    ):
        raise OriginError("Downloaded image lacks the requested repository digest")
    if image.get("Os") != "linux" or image.get("Architecture") != "amd64":
        raise OriginError("Downloaded image is not the release platform")
    config = image.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    expected = {
        "org.opencontainers.image.source": f"https://github.com/{REPOSITORY}",
        "org.opencontainers.image.revision": commit,
        "org.opencontainers.image.version": tag,
        "org.opencontainers.image.licenses": "MIT",
    }
    if not isinstance(labels, dict) or any(
        labels.get(key) != value for key, value in expected.items()
    ):
        raise OriginError("Downloaded image labels disagree with the release")


def check_statement(document, digest, predicate_type):
    """Check CLI-verified subjects and retain a usable SPDX inventory.

    This function is not a signature verifier. Its caller must first obtain an
    exit-zero response from gh with verification_command's identity constraints.
    """
    _digest(digest)
    if not isinstance(document, list) or not 1 <= len(document) <= 2:
        raise OriginError("Expected one or two verified attestations")
    inventories = []
    for entry in document:
        result = entry.get("verificationResult") if isinstance(entry, dict) else None
        statement = result.get("statement") if isinstance(result, dict) else None
        expected_subject = [{"name": IMAGE, "digest": {"sha256": digest[7:]}}]
        if (
            not isinstance(statement, dict)
            or statement.get("_type") != "https://in-toto.io/Statement/v1"
            or statement.get("predicateType") != predicate_type
            or statement.get("subject") != expected_subject
        ):
            raise OriginError("Verified statement does not identify the exact image")
        if predicate_type == SBOM:
            inventory = statement.get("predicate")
            packages = (
                inventory.get("packages") if isinstance(inventory, dict) else None
            )
            if (
                not isinstance(inventory, dict)
                or inventory.get("spdxVersion") != "SPDX-2.3"
                or inventory.get("SPDXID") != "SPDXRef-DOCUMENT"
                or not isinstance(packages, list)
                or not packages
                or any(
                    not isinstance(package, dict)
                    or not isinstance(package.get("name"), str)
                    or not package["name"]
                    or not isinstance(package.get("SPDXID"), str)
                    or not package["SPDXID"].startswith("SPDXRef-")
                    for package in packages
                )
            ):
                raise OriginError("Verified SBOM lacks the expected SPDX inventory")
            inventories.append(inventory)
    if inventories and any(item != inventories[0] for item in inventories[1:]):
        raise OriginError("Verified SBOM attestations disagree")
    return inventories[0] if inventories else None


def verify_container(digest, commit, tag, *, expected_sbom=None, run_json=_run_json):
    """Verify origin and inventory of an already downloaded release image."""
    _digest(digest)
    _release_identity(commit, tag)
    local = run_json(["docker", "image", "inspect", f"{IMAGE}@{digest}"])
    check_local_image(local, digest, commit, tag)
    provenance = run_json(verification_command(digest, commit, tag, PROVENANCE))
    check_statement(provenance, digest, PROVENANCE)
    sbom_result = run_json(verification_command(digest, commit, tag, SBOM))
    inventory = check_statement(sbom_result, digest, SBOM)
    if expected_sbom is not None and inventory != expected_sbom:
        raise OriginError("Downloaded SBOM disagrees with the signed inventory")
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "workflow": ".github/workflows/release.yml",
        "source_commit": commit,
        "source_ref": f"refs/tags/{tag}",
        "image": IMAGE,
        "digest": digest,
        "platform": "linux/amd64",
        "downloaded_image_checked": True,
        "provenance_verified": True,
        "sbom_verified": True,
        "sbom_package_count": len(inventory["packages"]),
        "sbom": inventory,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="Record the Buildx manifest digest")
    record.add_argument("--metadata", type=Path, required=True)
    record.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="Verify an already downloaded image")
    verify.add_argument("--digest", required=True)
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument(
        "--sbom", type=Path, help="Also match this downloaded SPDX file"
    )
    for command in (record, verify):
        command.add_argument("--commit", required=True)
        command.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise OriginError("Output already exists")
        if args.command == "record":
            with args.metadata.open("rb") as source:
                metadata = parse_json(source.read(MAX_JSON_BYTES + 1))
            document = release_record(metadata, args.commit, args.tag)
        else:
            expected_sbom = None
            if args.sbom is not None:
                with args.sbom.open("rb") as source:
                    expected_sbom = parse_json(source.read(MAX_JSON_BYTES + 1))
                if not isinstance(expected_sbom, dict):
                    raise OriginError("Downloaded SBOM must be an object")
            document = verify_container(
                args.digest, args.commit, args.tag, expected_sbom=expected_sbom
            )
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(document, indent=2) + "\n")
        if args.command == "record":
            print(f"image={document['image']}")
            print(f"digest={document['digest']}")
            print(f"reference={document['reference']}")
        else:
            print("Verified downloaded image, build origin and SPDX inventory")
    except (OriginError, OSError) as error:
        print(f"Container origin check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
