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
    assert release["permissions"] == {"contents": "read", "packages": "write"}
    steps = release["jobs"]["publish-image"]["steps"]
    runs = [s.get("run", "") for s in steps]
    gate = next(i for i, r in enumerate(runs) if "make check" in r)
    push = next(i for i, r in enumerate(runs) if "docker push" in r)
    assert gate < push
    assert any("ghcr.io" in r for r in runs)


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
