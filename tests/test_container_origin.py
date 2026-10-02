"""Consumer policy checks using inert outputs, never registry credentials."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import container_origin as origin

DIGEST = "sha256:" + "a" * 64
COMMIT = "b" * 40
TAG = "v0.2.0"


def metadata():
    return {
        "containerimage.digest": DIGEST,
        "containerimage.config.digest": "sha256:" + "c" * 64,
        "containerimage.descriptor": {
            "digest": DIGEST,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
        },
    }


def downloaded_image():
    return [
        {
            "RepoDigests": [f"{origin.IMAGE}@{DIGEST}"],
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {
                "Labels": {
                    "org.opencontainers.image.source": "https://github.com/"
                    + origin.REPOSITORY,
                    "org.opencontainers.image.revision": COMMIT,
                    "org.opencontainers.image.version": TAG,
                    "org.opencontainers.image.licenses": "MIT",
                }
            },
        }
    ]


def attestation(predicate_type):
    predicate = (
        {
            "spdxVersion": "SPDX-2.3",
            "SPDXID": "SPDXRef-DOCUMENT",
            "packages": [
                {"SPDXID": "SPDXRef-Package-python", "name": "python3"},
                {"SPDXID": "SPDXRef-Package-fastapi", "name": "fastapi"},
            ],
        }
        if predicate_type == origin.SBOM
        else {"buildDefinition": {}, "runDetails": {}}
    )
    return [
        {
            "verificationResult": {
                "statement": {
                    "_type": "https://in-toto.io/Statement/v1",
                    "subject": [
                        {"name": origin.IMAGE, "digest": {"sha256": DIGEST[7:]}}
                    ],
                    "predicateType": predicate_type,
                    "predicate": predicate,
                }
            }
        }
    ]


def test_record_uses_manifest_and_never_config_digest():
    result = origin.release_record(metadata(), COMMIT, TAG)
    assert result["digest"] == DIGEST
    assert result["digest"] != metadata()["containerimage.config.digest"]
    assert result["reference"] == f"{origin.IMAGE}@{DIGEST}"


@pytest.mark.parametrize(
    "field", ["containerimage.digest", "containerimage.descriptor"]
)
def test_record_rejects_missing_digest_identity(field):
    value = metadata()
    del value[field]
    with pytest.raises(origin.OriginError):
        origin.release_record(value, COMMIT, TAG)


@pytest.mark.parametrize(
    "descriptor",
    [
        {"digest": "sha256:" + "c" * 64},
        {"digest": DIGEST, "mediaType": "application/vnd.oci.image.index.v1+json"},
        None,
    ],
)
def test_record_rejects_disagreement_and_unimplemented_index_contract(descriptor):
    value = metadata()
    value["containerimage.descriptor"] = descriptor
    with pytest.raises(origin.OriginError):
        origin.release_record(value, COMMIT, TAG)


@pytest.mark.parametrize(
    "digest", ["a" * 64, "sha512:" + "a" * 128, "sha256:" + "A" * 64, DIGEST + "\n"]
)
def test_rejects_noncanonical_digest_before_any_tool_call(digest):
    with pytest.raises(origin.OriginError):
        origin.verify_container(
            digest, COMMIT, TAG, run_json=lambda _argv: pytest.fail()
        )


@pytest.mark.parametrize(
    ("commit", "tag"),
    [
        ("b" * 39, TAG),
        (COMMIT.upper(), TAG),
        (COMMIT, "latest"),
        (COMMIT, "v1; echo x"),
    ],
)
def test_rejects_bad_release_identity_before_tool_call(commit, tag):
    with pytest.raises(origin.OriginError):
        origin.verify_container(
            DIGEST, commit, tag, run_json=lambda _argv: pytest.fail()
        )


def test_consumer_checks_both_types_with_complete_certificate_policy():
    calls = []

    def inert_tools(argv):
        calls.append(argv)
        if argv[0] == "docker":
            assert argv == ["docker", "image", "inspect", f"{origin.IMAGE}@{DIGEST}"]
            return downloaded_image()
        for flag, expected in {
            "--repo": origin.REPOSITORY,
            "--signer-workflow": origin.WORKFLOW,
            "--cert-identity": f"https://github.com/{origin.WORKFLOW}@refs/tags/{TAG}",
            "--cert-oidc-issuer": "https://token.actions.githubusercontent.com",
            "--source-ref": f"refs/tags/{TAG}",
            "--source-digest": COMMIT,
        }.items():
            assert argv[argv.index(flag) + 1] == expected
        assert argv[:4] == [
            "gh",
            "attestation",
            "verify",
            f"oci://{origin.IMAGE}@{DIGEST}",
        ]
        assert "--deny-self-hosted-runners" in argv
        assert "--bundle-from-oci" in argv
        assert argv[argv.index("--limit") + 1] == "2"
        assert argv[-2:] == ["--format", "json"]
        return attestation(argv[argv.index("--predicate-type") + 1])

    result = origin.verify_container(DIGEST, COMMIT, TAG, run_json=inert_tools)
    assert [call[0] for call in calls] == ["docker", "gh", "gh"]
    assert result["provenance_verified"] and result["sbom_verified"]
    assert result["sbom_package_count"] == 2


@pytest.mark.parametrize(
    "mutation",
    ["repository", "digest", "platform", "source", "commit", "version", "license"],
)
def test_downloaded_mismatch_stops_before_attestation(mutation):
    value = downloaded_image()
    image = value[0]
    if mutation == "repository":
        image["RepoDigests"] = [f"ghcr.io/another/project@{DIGEST}"]
    elif mutation == "digest":
        image["RepoDigests"] = [f"{origin.IMAGE}@sha256:" + "d" * 64]
    elif mutation == "platform":
        image["Architecture"] = "arm64"
    else:
        key = {
            "source": "source",
            "commit": "revision",
            "version": "version",
            "license": "licenses",
        }[mutation]
        image["Config"]["Labels"][f"org.opencontainers.image.{key}"] = "wrong"
    calls = []

    def inert_tools(argv):
        calls.append(argv)
        return value

    with pytest.raises(origin.OriginError):
        origin.verify_container(DIGEST, COMMIT, TAG, run_json=inert_tools)
    assert len(calls) == 1


@pytest.mark.parametrize("failed_type", [origin.PROVENANCE, origin.SBOM])
def test_failed_signature_verification_never_produces_success(failed_type):
    def inert_tools(argv):
        if argv[0] == "docker":
            return downloaded_image()
        predicate_type = argv[argv.index("--predicate-type") + 1]
        if predicate_type == failed_type:
            raise origin.OriginError("inert signature rejection")
        return attestation(predicate_type)

    with pytest.raises(origin.OriginError, match="signature rejection"):
        origin.verify_container(DIGEST, COMMIT, TAG, run_json=inert_tools)


@pytest.mark.parametrize(
    "mutation", ["subject_name", "subject_digest", "type", "predicate_type", "empty"]
)
def test_verified_subject_policy_rejects_mismatches(mutation):
    value = attestation(origin.PROVENANCE)
    statement = value[0]["verificationResult"]["statement"]
    if mutation == "subject_name":
        statement["subject"][0]["name"] = "ghcr.io/another/project"
    elif mutation == "subject_digest":
        statement["subject"][0]["digest"]["sha256"] = "c" * 64
    elif mutation == "type":
        statement["_type"] = "https://in-toto.io/Statement/v0.1"
    elif mutation == "predicate_type":
        statement["predicateType"] = origin.SBOM
    else:
        value = []
    with pytest.raises(origin.OriginError):
        origin.check_statement(value, DIGEST, origin.PROVENANCE)


@pytest.mark.parametrize("mutation", ["version", "document_id", "empty", "name", "id"])
def test_signed_but_unusable_inventory_is_rejected(mutation):
    value = attestation(origin.SBOM)
    inventory = value[0]["verificationResult"]["statement"]["predicate"]
    if mutation == "version":
        inventory["spdxVersion"] = "SPDX-1.2"
    elif mutation == "document_id":
        inventory["SPDXID"] = "wrong"
    elif mutation == "empty":
        inventory["packages"] = []
    elif mutation == "name":
        del inventory["packages"][0]["name"]
    else:
        inventory["packages"][0]["SPDXID"] = "wrong"
    with pytest.raises(origin.OriginError):
        origin.check_statement(value, DIGEST, origin.SBOM)


def test_conflicting_verified_inventories_are_rejected():
    value = attestation(origin.SBOM)
    value.append(copy.deepcopy(value[0]))
    value[1]["verificationResult"]["statement"]["predicate"]["packages"].pop()
    with pytest.raises(origin.OriginError, match="disagree"):
        origin.check_statement(value, DIGEST, origin.SBOM)


def test_separate_downloaded_sbom_must_match_verified_inventory():
    def inert_tools(argv):
        if argv[0] == "docker":
            return downloaded_image()
        return attestation(argv[argv.index("--predicate-type") + 1])

    inventory = attestation(origin.SBOM)[0]["verificationResult"]["statement"][
        "predicate"
    ]
    assert (
        origin.verify_container(
            DIGEST, COMMIT, TAG, expected_sbom=inventory, run_json=inert_tools
        )["sbom"]
        == inventory
    )
    different = copy.deepcopy(inventory)
    different["packages"][0]["name"] = "another-package"
    with pytest.raises(origin.OriginError, match="signed inventory"):
        origin.verify_container(
            DIGEST, COMMIT, TAG, expected_sbom=different, run_json=inert_tools
        )


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b"{", b"\xff"])
def test_rejects_ambiguous_or_malformed_json(raw):
    with pytest.raises(origin.OriginError):
        origin.parse_json(raw)


def test_json_size_limit(monkeypatch):
    monkeypatch.setattr(origin, "MAX_JSON_BYTES", 4)
    with pytest.raises(origin.OriginError, match="size limit"):
        origin.parse_json(b"[1,2]")


def test_actual_inert_subprocess_json_and_failure_redaction():
    assert origin._run_json([sys.executable, "-c", "print('[1,2]')"]) == [1, 2]
    with pytest.raises(origin.OriginError) as caught:
        origin._run_json(
            [
                sys.executable,
                "-c",
                "import sys; print('synthetic-private-sentinel', file=sys.stderr); "
                "sys.exit(3)",
            ]
        )
    assert "synthetic-private-sentinel" not in str(caught.value)


def test_subprocess_timeout_is_a_failure(monkeypatch):
    def timeout(argv, **kwargs):
        assert kwargs["timeout"] == 120 and kwargs["stdin"] == subprocess.DEVNULL
        assert "shell" not in kwargs
        raise subprocess.TimeoutExpired(argv, 120)

    monkeypatch.setattr(origin.subprocess, "run", timeout)
    with pytest.raises(origin.OriginError, match="could not complete"):
        origin._run_json(["gh", "attestation", "verify"])


def test_record_command_is_exclusive_and_emits_only_identity(tmp_path, capsys):
    source = tmp_path / "metadata.json"
    source.write_text(json.dumps(metadata()), encoding="utf-8")
    target = tmp_path / "image.json"
    argv = [
        "record",
        "--metadata",
        str(source),
        "--commit",
        COMMIT,
        "--tag",
        TAG,
        "--output",
        str(target),
    ]
    assert origin.main(argv) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"image={origin.IMAGE}",
        f"digest={DIGEST}",
        f"reference={origin.IMAGE}@{DIGEST}",
    ]
    original = target.read_bytes()
    assert origin.main(argv) == 1
    assert target.read_bytes() == original


def test_actual_record_entrypoint_writes_manifest_identity(tmp_path):
    source = tmp_path / "metadata.json"
    source.write_text(json.dumps(metadata()), encoding="utf-8")
    target = tmp_path / "image.json"
    script = Path(origin.__file__).resolve()
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "record",
            "--metadata",
            str(source),
            "--commit",
            COMMIT,
            "--tag",
            TAG,
            "--output",
            str(target),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.splitlines()[1] == f"digest={DIGEST}"
    assert json.loads(target.read_bytes())["reference"] == f"{origin.IMAGE}@{DIGEST}"


def test_verify_cli_failure_writes_no_success_receipt(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise origin.OriginError("inert rejection")

    monkeypatch.setattr(origin, "verify_container", fail)
    target = tmp_path / "consumer.json"
    assert (
        origin.main(
            [
                "verify",
                "--digest",
                DIGEST,
                "--commit",
                COMMIT,
                "--tag",
                TAG,
                "--output",
                str(target),
            ]
        )
        == 1
    )
    assert not target.exists()
