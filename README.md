# relayna-protocol

Establish transport-independent contracts before implementing more integrations

## Core architecture

Relayna Protocol is the shared specification for the Relayna ecosystem.

![Relayna Protocol defines versioned Protobuf, JSON Schema, and OpenAPI contracts shared by Gateway (Rust), SDK (Python), Spooltail (Rust), Cell (Go), and Studio (Python and TypeScript).](docs/architecture.svg)

## Protocol v1.0

The single-task profile specifies how Gateway, the Python SDK, Spooltail, and Studio exchange tasks, status, and results, with optional Cell execution correlation. Start with the [protocol guide](contracts/v1.0/README.md) or the [SDK service OpenAPI document](contracts/v1.0/openapi.yaml).

Run the contract checks with Python 3.12 or newer. The dependencies are for verification in a development environment.

```sh
python -m pip install -r tests/requirements.txt
python -m unittest discover -s tests -v
```
