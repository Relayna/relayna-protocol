"""Provision and exercise the Relayna reference stack through its existing APIs."""

import argparse
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

DEPLOY = Path(__file__).resolve().parent
REGISTRATION = "reference"
PREFIX = f"/services/{REGISTRATION}"


def request(url, path, *, token=None, payload=None, method="GET", expected=200, headers=None):
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    call = Request(url + path, data=data, headers=headers, method=method)
    try:
        response = urlopen(call, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        if response.status != expected:
            raise RuntimeError(f"{method} {path} returned HTTP {response.status}, expected {expected}")
        body = response.read()
        if not body:
            return None
        if response.headers.get_content_type() == "application/json":
            return json.loads(body)
        return body.decode()


def until_ready(url, path):
    for _ in range(60):
        try:
            request(url, path)
            return
        except (RuntimeError, URLError):
            time.sleep(1)
    raise RuntimeError(f"{url}{path} did not become ready")


def provision_gateway(control, admin_token, upstream, key_file):
    def admin(path, **kwargs):
        return request(control, "/admin-ui/admin/" + path, token=admin_token, **kwargs)

    name = "Relayna local reference"
    project = next((item for item in admin("projects") if item["name"] == name), None)
    if project is None:
        project = admin("projects", method="POST", payload={"name": name})
    project_id = project["id"]
    policy = {
        "allowed_routes": ["/services/*"],
        "allowed_providers": ["internal-service"],
        "allowed_services": [REGISTRATION],
        "allow_streaming": True,
    }
    layer = next(
        (item for item in admin("policy-layers") if item["kind"] == "project" and item["scope_id"] == project_id),
        None,
    )
    if layer is None:
        admin("policy-layers", method="POST", payload={"kind": "project", "scope_id": project_id, "policy": policy})
    elif any(layer["policy"].get(field) != value for field, value in policy.items()):
        raise RuntimeError("The existing reference project policy differs from the quickstart policy")

    registered = next((service for service in admin("services") if service["name"] == REGISTRATION), None)
    if registered is None:
        registered = admin(
            "services",
            method="POST",
            payload={
                "name": REGISTRATION,
                "project_id": project_id,
                "route_pattern": PREFIX + "/*",
                "upstream_base_url": upstream,
                "credential": "reference-local-sdk",
                "access": {"relayna_tasks": True},
                "allowed_methods": ["GET", "POST"],
                "timeout_ms": 30_000,
                "cost_mode": "none",
            },
        )
    if (
        registered["project_id"] != project_id
        or registered["upstream_base_url"].rstrip("/") != upstream
        or not registered["access"].get("relayna_tasks")
        or not registered["enabled"]
    ):
        raise RuntimeError("The existing reference registration differs from the quickstart registration")

    saved = next((item for item in admin("keys") if item["name"] == name), None)
    if saved is not None:
        if saved["project_id"] != project_id or saved["disabled"] or saved["revoked_at"] is not None:
            raise RuntimeError("The saved reference key is inactive or belongs to another project")
        if not key_file.is_file() or not key_file.read_text().strip().startswith(saved["key_prefix"]):
            raise RuntimeError("The reference key exists in Gateway. Supply its original private key file.")
        return key_file.read_text().strip()

    key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise RuntimeError(f"{key_file} exists but Gateway has no reference key. Use a new --key-file.") from error
    try:
        with os.fdopen(fd, "w") as output:
            created = admin(
                "keys",
                method="POST",
                payload={
                    "name": name,
                    "owner_type": "project",
                    "project_id": project_id,
                    "service_names": [REGISTRATION],
                    "policy": policy,
                },
            )
            output.write(created["raw_key"] + "\n")
    except Exception:
        key_file.unlink()
        raise
    return created["raw_key"]


def register_studio(studio, token, upstream):
    services = request(studio, "/studio/services", token=token)["services"]
    model = next((item for item in services if item["service_id"] == REGISTRATION), None)
    if model is None:
        model = request(
            studio,
            "/studio/services",
            token=token,
            method="POST",
            expected=201,
            payload={
                "service_id": REGISTRATION,
                "name": "Relayna local reference",
                "base_url": upstream,
                "environment": "local",
                "auth_mode": "internal_network",
            },
        )
    if model["base_url"].rstrip("/") != upstream or model["auth_mode"] != "internal_network":
        raise RuntimeError("Studio's existing reference service has a different upstream or auth mode")
    model = request(studio, "/studio/services/reference/refresh", token=token, method="POST")
    flags = model["capabilities"]["feature_flags"]
    require("spooltail.task_history.v1" in flags, "Studio discovered the task history profile")
    return flags


def parse_sse(stream):
    frames = []
    for frame in stream.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line and not line.startswith(":"))
        if "event" in fields:
            frames.append((fields["event"], fields.get("id"), json.loads(fields["data"])))
    return frames


def require(condition, description):
    if not condition:
        raise RuntimeError(f"Reference check failed: {description}")


def exercise(gateway, key, studio, studio_token, flags, baseline, studio_usage):
    def proxy(path, **kwargs):
        return request(gateway, PREFIX + path, token=key, **kwargs)

    def events(task_id, cursor=None):
        headers = {"Accept": "text/event-stream"}
        if cursor:
            headers["Last-Event-ID"] = cursor
        return parse_sse(proxy("/events/" + task_id, headers=headers))

    task_id = "reference-" + uuid4().hex
    payload = {"number": 21}
    work = {"task_id": task_id, "payload": payload}
    request(gateway, PREFIX + "/tasks", method="POST", payload=work, expected=401)
    for _ in range(2):
        accepted = proxy("/tasks", method="POST", payload=work, expected=202)
        require(accepted == {"task_id": task_id}, "submission and retry acknowledge the same task ID")
    proxy("/tasks", method="POST", payload=work | {"payload": {"number": 22}}, expected=409)

    frames = events(task_id)
    statuses = [(event_id, body) for kind, event_id, body in frames if kind == "status"]
    require(bool(statuses), "SSE carries a status timeline")
    require(all(event_id == body["event_id"] for event_id, body in statuses), "SSE IDs equal status event IDs")
    first_id, _ = statuses[0]
    latest_id, terminal = statuses[-1]
    require(terminal["status"] == "completed" and terminal["result"] == {"echo": payload}, "worker echoed the task")
    require(sum(body["status"] == "completed" for _, body in statuses) == 1, "one terminal status after retry")
    require(len({body["attempt_id"] for _, body in statuses}) == 1, "status timeline retains one attempt identity")
    latest = proxy("/status/" + task_id)
    require(latest == {"task_id": task_id, "event": terminal}, "latest status agrees with SSE")

    cursor = "missing-" + uuid4().hex
    history = proxy("/history/" + task_id)
    require(history["events"] == [body for _, body in statuses], "broker history agrees with SSE")
    after = "?" + urlencode({"after": first_id})
    require(proxy("/history/" + task_id + after)["events"] == history["events"][1:], "history resumes exclusively")
    resumed = [event_id for kind, event_id, _ in events(task_id, first_id) if kind == "status"]
    require(resumed == [event_id for event_id, _ in statuses[1:]] and latest_id in resumed, "SSE resumes exclusively")
    gap = proxy("/history/" + task_id + "?" + urlencode({"after": cursor}), expected=410)
    require(gap == {"detail": {"earliest_event_id": first_id}}, "history reports the retained replay boundary")
    require(events(task_id, cursor) == [("gap", None, {"earliest_event_id": first_id})], "SSE reports gap with no cursor")

    detail = request(studio, f"/studio/tasks/{REGISTRATION}/{task_id}", token=studio_token)
    require(detail["latest_status"]["event"] == terminal, "Studio reads the same terminal result")
    projected = detail["history"]["events"]
    require(len(projected) == history["count"], "Studio reads the same number of broker events")
    for source, event in zip(history["events"], projected, strict=True):
        require(all(event.get(field) == value for field, value in source.items()), "Studio preserves broker event data")

    if not baseline:
        require(events(task_id, latest_id) == [("ready", None, {})], "terminal cursor resumes without replay")
        broker = detail["history"].get("broker_stats")
        require(broker is not None and broker["broker"] == "spooltail", "Studio reads the live broker profile")
        require("spooltail.cancel.v1" in flags, "the reference service advertises cancellation")
        capability = proxy("/relayna/capabilities")
        require("spooltail.cancel.v1" in capability["feature_flags"], "Gateway serves the task capability profile")
        proxy("/tasks/" + cursor + "/cancel", method="POST", expected=404)
        proxy("/tasks/" + task_id + "/cancel", method="POST", expected=409)
        usage = proxy("/tasks/" + task_id + "/usage")
        require(usage["task_id"] == task_id and usage["service"] == REGISTRATION, "Gateway usage is scoped to the same task")
        if studio_usage:
            projected_usage = detail["history"].get("gateway_usage")
            require(projected_usage is not None and projected_usage["source_available"], "Studio reaches Gateway usage")
    print(f"Reference workflow passed for {task_id}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://127.0.0.1:18380")
    parser.add_argument("--control", default="http://127.0.0.1:18381")
    parser.add_argument("--studio", default="http://127.0.0.1:18382")
    parser.add_argument("--sdk-upstream", default="http://sdk:8080")
    parser.add_argument("--studio-sdk-upstream", help="Override the SDK address seen by Studio")
    parser.add_argument("--env-file", type=Path, default=DEPLOY / ".env")
    parser.add_argument("--key-file", type=Path, default=DEPLOY / ".local/reference.key")
    parser.add_argument("--baseline", action="store_true", help="Check the earlier single-task revisions")
    args = parser.parse_args()
    local = {}
    if args.env_file.is_file():
        for line in args.env_file.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                name, value = line.split("=", 1)
                local[name] = value
    admin = os.getenv("RELAYNA_GATEWAY_ADMIN_TOKEN", local.get("RELAYNA_GATEWAY_ADMIN_TOKEN"))
    studio_token = os.getenv("RELAYNA_STUDIO_OPERATOR_TOKEN", local.get("RELAYNA_STUDIO_OPERATOR_TOKEN"))
    studio_usage = bool(os.getenv("RELAYNA_STUDIO_GATEWAY_USAGE_TOKEN", local.get("RELAYNA_STUDIO_GATEWAY_USAGE_TOKEN")))
    if not admin or not studio_token:
        parser.error("Supply the Gateway and Studio operator credentials")
    control, studio, gateway, upstream = (value.rstrip("/") for value in (
        args.control, args.studio, args.gateway, args.sdk_upstream,
    ))
    until_ready(control, "/admin-ui/readyz")
    until_ready(studio, "/studio/auth/config")
    key = provision_gateway(control, admin, upstream, args.key_file)
    flags = register_studio(studio, studio_token, (args.studio_sdk_upstream or upstream).rstrip("/"))
    exercise(gateway, key, studio, studio_token, flags, args.baseline, studio_usage)


if __name__ == "__main__":
    main()
