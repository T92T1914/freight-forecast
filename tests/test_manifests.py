"""Structural tests for the k8s manifests and CI workflow.

No cluster or Docker daemon exists in every dev environment, so these pin
the contracts a live system would enforce: probes point at a real endpoint,
ports line up end to end, and the service selector actually matches the
deployment's pod labels (the classic silent k8s failure).
"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(rel):
    with open(ROOT / rel, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _workflow(rel):
    wf = _load(rel)
    # PyYAML follows YAML 1.1, where a bare `on` is the boolean True
    wf["on"] = wf.pop(True, wf.get("on"))
    return wf


def test_deployment_probes_hit_the_real_health_endpoint():
    dep = _load("k8s/deployment.yaml")
    container = dep["spec"]["template"]["spec"]["containers"][0]
    for probe in ("readinessProbe", "livenessProbe"):
        http = container[probe]["httpGet"]
        assert http["path"] == "/health"
        assert http["port"] == 8000


def test_ports_line_up_end_to_end():
    dep = _load("k8s/deployment.yaml")
    svc = _load("k8s/service.yaml")
    container = dep["spec"]["template"]["spec"]["containers"][0]
    container_port = container["ports"][0]["containerPort"]
    assert container_port == 8000  # uvicorn's port in the Dockerfile CMD
    assert svc["spec"]["ports"][0]["targetPort"] == container_port


def test_service_selector_matches_pod_labels():
    dep = _load("k8s/deployment.yaml")
    svc = _load("k8s/service.yaml")
    pod_labels = dep["spec"]["template"]["metadata"]["labels"]
    for key, value in svc["spec"]["selector"].items():
        assert pod_labels.get(key) == value
    assert dep["spec"]["selector"]["matchLabels"] == pod_labels


def test_deployment_sets_resource_limits():
    dep = _load("k8s/deployment.yaml")
    resources = dep["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert "requests" in resources and "limits" in resources


def test_ci_gates_image_build_on_lint_and_tests():
    ci = _workflow(".github/workflows/ci.yml")
    jobs = ci["jobs"]
    assert {"lint", "test", "build-image"} <= set(jobs)
    assert set(jobs["build-image"]["needs"]) == {"lint", "test"}


def test_ci_token_is_read_only():
    ci = _workflow(".github/workflows/ci.yml")
    assert ci["permissions"] == {"contents": "read"}


def test_release_publishes_only_on_version_tags():
    """Publishing is a side effect with an audience, so the trigger and the
    token scope are contracts worth pinning: version tags only, packages
    writable, and the tests run again before anything is pushed."""
    release = _workflow(".github/workflows/release.yml")
    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert release["permissions"] == {"contents": "read"}
    job = release["jobs"]["publish-image"]
    assert job["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert job["if"] == "github.repository == 'T92T1914/freight-forecast'"
    steps = job["steps"]
    runs = [s.get("run", "") for s in steps]
    gate = next(i for i, r in enumerate(runs) if "make check" in r)
    push = next(i for i, r in enumerate(runs) if "docker buildx build --push" in r)
    assert gate < push
    assert any("ghcr.io" in r for r in runs)


def test_release_binds_inventory_attestations_and_consumer_to_one_digest():
    release = _workflow(".github/workflows/release.yml")
    steps = release["jobs"]["publish-image"]["steps"]
    build = next(step for step in steps if step.get("id") == "image")
    assert "--metadata-file release-evidence/buildx.json" in build["run"]
    assert "--platform linux/amd64 --provenance=false" in build["run"]
    assert "container_origin.py record" in build["run"]
    assert "--all-tags" not in build["run"]
    inventory = next(
        step
        for step in steps
        if step.get("name") == "Inventory the exact published image"
    )
    assert inventory["env"]["IMAGE_REFERENCE"] == "${{ steps.image.outputs.reference }}"
    assert "syft_1.54.0_linux_amd64.tar.gz" in inventory["run"]
    assert (
        "54a87372498168b2d033e876fd41fa4e8035b872699e525a57046e1f2f09c860"
        in inventory["run"]
    )
    assert inventory["run"].index("sha256sum --check --status") < inventory[
        "run"
    ].index("tar -xzf")
    assert 'scan "registry:$IMAGE_REFERENCE"' in inventory["run"]
    assert "spdx-json=release-evidence/sbom.spdx.json" in inventory["run"]
    attestations = [step for step in steps if "actions/attest@" in step.get("uses", "")]
    assert len(attestations) == 2
    for step in attestations:
        assert step["with"]["subject-name"] == "${{ steps.image.outputs.image }}"
        assert step["with"]["subject-digest"] == "${{ steps.image.outputs.digest }}"
        assert step["with"]["push-to-registry"] is True
        assert step["with"]["create-storage-record"] is False
    assert "sbom-path" not in attestations[0]["with"]
    assert attestations[1]["with"]["sbom-path"] == "release-evidence/sbom.spdx.json"
    consumer = next(
        step for step in steps if "container_origin.py verify" in step.get("run", "")
    )
    assert steps.index(attestations[-1]) < steps.index(consumer)
    assert 'docker pull --platform linux/amd64 "$IMAGE_REFERENCE"' in consumer["run"]
    assert "--commit" in consumer["run"] and "--tag" in consumer["run"]
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])


def test_pods_are_annotated_for_prometheus_scraping():
    dep = _load("k8s/deployment.yaml")
    annotations = dep["spec"]["template"]["metadata"]["annotations"]
    assert annotations["prometheus.io/scrape"] == "true"
    assert annotations["prometheus.io/path"] == "/metrics"
    assert int(annotations["prometheus.io/port"]) == 8000


def test_dashboard_queries_only_metrics_the_app_exports():
    with open(ROOT / "monitoring/grafana-dashboard.json", encoding="utf-8") as f:
        dashboard = json.load(f)
    with open(ROOT / "src/serve.py", encoding="utf-8") as f:
        serve_src = f.read()

    exprs = [t["expr"] for p in dashboard["panels"] for t in p["targets"]]
    assert exprs, "dashboard has no queries"
    for expr in exprs:
        for metric in re.findall(r"[a-z_]+_(?:total|bucket)", expr):
            base = re.sub(r"_(total|bucket)$", "", metric)
            assert base in serve_src, f"dashboard queries unknown metric {metric}"


def test_local_monitoring_binds_only_loopback_and_limits_api():
    services = _load("monitoring/docker-compose.yml")["services"]
    for service in services.values():
        assert all(port.startswith("127.0.0.1:") for port in service["ports"])
    api = services["api"]
    assert api["read_only"] is True
    assert api["cap_drop"] == ["ALL"]
    assert api["security_opt"] == ["no-new-privileges:true"]
    assert api["cpus"] == 0.5
    assert api["mem_limit"] == "512m"
    assert api["pids_limit"] == 64


def test_image_retains_training_gate_before_narrow_nonroot_model_access():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    training = dockerfile.index("RUN python -m src.train")
    ownership = dockerfile.index(
        "chown 10001:10001 /app/models /app/models/model.joblib"
    )
    user = dockerfile.index("USER 10001:10001")
    assert training < ownership < user
    assert "chmod 0400 /app/models/model.joblib" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
    for variable in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        assert variable + "=1" in dockerfile
    command = json.loads("[" + dockerfile.rsplit("CMD [", 1)[1].strip())
    for flag, value in (
        ("--workers", "1"),
        ("--limit-concurrency", "8"),
        ("--timeout-graceful-shutdown", "5"),
    ):
        assert command[command.index(flag) + 1] == value


def test_make_run_keeps_the_declared_local_container_policy():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    run = makefile.split("run: image", 1)[1].split("\n\n", 1)[0]
    for argument in (
        "127.0.0.1:8000:8000",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=0.5",
        "--memory=512m",
        "--pids-limit=64",
        "--stop-timeout=5",
    ):
        assert argument in run
