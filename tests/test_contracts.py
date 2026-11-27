import json
import unittest
from pathlib import Path
from urllib.parse import urljoin

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from openapi_spec_validator import validate as validate_openapi
from openapi_spec_validator.readers import read_from_filename
from referencing import Registry
from referencing.jsonschema import DRAFT202012


CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "v1.0"
OPENAPI, OPENAPI_URI = read_from_filename(str(CONTRACT / "openapi.yaml"))
SCHEMAS = {
    path.name: json.loads(path.read_text(encoding="utf-8"))
    for path in (CONTRACT / "schemas").glob("*.schema.json")
}
TASK_SCHEMA = "task-envelope.schema.json"
STATUS_SCHEMA = "status-event.schema.json"
REGISTRY = Registry().with_resources(
    ((CONTRACT / "schemas" / name).as_uri(), DRAFT202012.create_resource(schema))
    for name, schema in SCHEMAS.items()
).with_resource(OPENAPI_URI, DRAFT202012.create_resource(OPENAPI))


def check(instance, ref, base_uri=OPENAPI_URI):
    validator = Draft202012Validator(
        {"$ref": urljoin(base_uri, ref)},
        registry=REGISTRY,
        format_checker=FormatChecker(),
    )
    validator.validate(instance)


class ContractTests(unittest.TestCase):
    def test_documents_and_transport_examples(self):
        validate_openapi(OPENAPI, base_uri=OPENAPI_URI)
        for name, schema in SCHEMAS.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
                for example in schema.get("examples", []):
                    check(example, (CONTRACT / "schemas" / name).as_uri())

        check(
            SCHEMAS[TASK_SCHEMA]["examples"][0],
            "schemas/task-envelope.schema.json#/$defs/spooltailWorkRecord",
        )

    def test_http_json_examples(self):
        for path, methods in OPENAPI["paths"].items():
            for method in ("get", "post"):
                if method not in methods:
                    continue
                operation = methods[method]
                contents = list(operation["responses"].items())
                if "requestBody" in operation:
                    contents.append(("request", operation["requestBody"]))

                for label, content in contents:
                    media = content.get("content", {}).get("application/json")
                    if media is None:
                        continue
                    with self.subTest(path=path, method=method, message=label):
                        examples = (
                            [media["example"]]
                            if "example" in media
                            else [example["value"] for example in media["examples"].values()]
                        )
                        for example in examples:
                            check(example, media["schema"]["$ref"])
                            if path == "/status/{task_id}" and label == "200":
                                self.assertEqual(example["task_id"], example["event"]["task_id"])
                            if path == "/history/{task_id}" and label == "200":
                                self.assertEqual(example["count"], len(example["events"]))
                                for event in example["events"]:
                                    self.assertEqual(example["task_id"], event["task_id"])

    def test_sse_examples(self):
        media = OPENAPI["paths"]["/events/{task_id}"]["get"]["responses"]["200"]["content"]["text/event-stream"]
        event_types = media["x-relayna-sse-events"]
        streams = {}
        for name, example in media["examples"].items():
            with self.subTest(stream=name):
                stream = example["value"]
                self.assertTrue(stream.endswith("\n\n"), "The last SSE frame must be dispatched")
                messages = []
                for frame in stream.strip("\n").split("\n\n"):
                    fields = dict(line.split(": ", 1) for line in frame.splitlines())
                    kind = fields["event"]
                    data = json.loads(fields["data"])
                    event_type = event_types[kind]
                    check(data, event_type["dataSchema"]["$ref"])
                    if "idFrom" in event_type:
                        self.assertEqual(fields["id"], data[event_type["idFrom"]])
                    else:
                        self.assertNotIn("id", fields)
                    messages.append((kind, data))
                streams[name] = messages

        self.assertEqual([kind for kind, _ in streams["status"]], ["ready", "status", "status"])
        self.assertEqual([kind for kind, _ in streams["gap"]], ["gap"])
        first_event = streams["status"][1][1]
        last_event = streams["status"][-1][1]
        self.assertEqual(streams["gap"][0][1]["earliest_event_id"], first_event["event_id"])
        self.assertEqual(first_event["task_id"], last_event["task_id"])
        self.assertNotEqual(first_event["event_id"], last_event["event_id"])
        self.assertEqual(last_event["status"], "completed")
        latest = OPENAPI["paths"]["/status/{task_id}"]["get"]["responses"]["200"]["content"]["application/json"]
        self.assertEqual(last_event, latest["example"]["event"])
        check({"earliest_event_id": None}, "#/components/schemas/Gap")
        check({"error": "Status subscription unavailable"}, "#/components/schemas/StreamError")

    def test_submission_rejects_unsafe_ids_and_unversioned_shapes(self):
        valid = {"task_id": "a" + "_-~.0" * 51, "payload": {}}
        ref = "#/components/schemas/TaskSubmission"
        check({**valid, "service": None, "task_type": None, "correlation_id": None}, ref)
        check({**valid, "created_at": "2026-11-27T21:49:35+07:00", "spec_version": "1.0"}, ref)
        invalid = [
            {"payload": {}},
            {"task_id": "task-42"},
            *({**valid, "task_id": value} for value in ("", ".hidden", "a/b", "a%2Fb", "a\n", "a" * 257)),
            {**valid, "payload": []},
            {**valid, "created_at": "2026-11-27T14:49:35"},
            {**valid, "created_at": None},
            {**valid, "spec_version": None},
            {**valid, "spec_version": "2.0"},
            {**valid, "event_id": "custom"},
            {**valid, "priority": 0},
            {**valid, "attempt_id": "caller-attempt"},
        ]
        for instance in invalid:
            with self.subTest(instance=instance), self.assertRaises(ValidationError):
                check(instance, ref)

    def test_wire_records_preserve_extensions_and_validate_reserved_fields(self):
        task = SCHEMAS[TASK_SCHEMA]["examples"][0]
        status = SCHEMAS[STATUS_SCHEMA]["examples"][0]
        work_ref = "schemas/task-envelope.schema.json#/$defs/spooltailWorkRecord"
        status_ref = "#/components/schemas/StatusEvent"
        history_ref = "#/components/schemas/TaskHistory"
        check({**task, "future_extension": {"enabled": True}}, work_ref)
        check({**status, "status": "enriching", "future_extension": True}, status_ref)
        check({**status, "meta": {"cell": {"cell_id": "cell-y4"}}}, status_ref)
        invalid = [
            (work_ref, {**task, "event_id": "custom"}),
            (status_ref, {**status, "result": []}),
            (status_ref, {**status, "event_id": "split\nframe"}),
            (status_ref, {key: value for key, value in status.items() if key != "attempt_id"}),
            (status_ref, {**status, "meta": {"cell": {"execution_id": "exec-p2"}}}),
            (history_ref, {"task_id": "task-42", "count": 64, "events": [status] * 65}),
        ]
        for ref, instance in invalid:
            with self.subTest(ref=ref), self.assertRaises(ValidationError):
                check(instance, ref)
