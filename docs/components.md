# Component releases

The component repositories each own their release channel. The development stack selects a compatible set, and GitHub access follows the existing access policies for each component.

| Component | Channel | Selection |
| --- | --- | --- |
| Protocol | [Versioned specs](https://github.com/Relayna/relayna-protocol) | Use the published contract directory at a Git commit. |
| Gateway | [Gateway releases](https://github.com/Relayna/relayna-gateway/releases) | Use the source revision or an OCI manifest digest from the existing Gateway registry. |
| Python SDK | [SDK releases](https://github.com/Relayna/relayna-sdk/releases) | Use the selected SDK package and matching reference app. |
| Studio | [Studio releases](https://github.com/Relayna/relayna-studio/releases) | Use the paired Studio backend and frontend artifacts. Both images build from the pinned Studio revision. The backend resolves the SDK from its own lockfile. |
| Spooltail | [Broker and Python client releases](https://github.com/Relayna/relayna-spooltail/releases) | Select the broker separately from its portable Python wheel. The reference build uses the portable package at the recorded broker source revision. Cancellation requires a broker with atomic `cancelled` support. |
| Cell | [Cell releases](https://github.com/Relayna/relayna-cell/releases) | Install the standalone launcher and reference OCI image from the recorded source revision or use a compatible release. |

The independent release workflow uses `sdk-vX.Y.Z` for the SDK and `studio-vX.Y.Z` for Studio. Earlier combined releases are in the [original release archive](https://github.com/sarattha/relayna/releases). Cell uses `cell-vX.Y.Z` for its standalone Linux archives and publishes separate AMD64 and ARM64 checksums. The reference source lock records one compatible set of source revisions.

## Reproduce a selection

[Relayna Deploy](https://github.com/Relayna/relayna-deploy) maintains the shared installers and their source lock. The lock below pins Protocol's bundled compatibility fixture.

`deploy/sources.json` records full SHA-1 commit IDs. `python3 deploy/build.py --lock <file>` accepts a reviewed replacement lock with the same structure. The builder fetches those revisions and labels the resulting images with their provenance. Gateway and Studio use their upstream Dockerfiles. To redeploy the exact binaries later, retain image IDs for local builds or OCI manifest digests for registry artifacts. Source builds may use upstream base tags and package mirrors. Record an image digest to reproduce the resulting artifact byte for byte.

Set `RELAYNA_GATEWAY_IMAGE`, `RELAYNA_SDK_IMAGE`, `RELAYNA_SPOOLTAIL_IMAGE`, `RELAYNA_STUDIO_BACKEND_IMAGE`, and `RELAYNA_STUDIO_IMAGE` in `deploy/.env` to preexisting local tags or `repository@sha256:…` refs for Compose. Pull digest references with your existing registry access before starting the stack. For Helm, set the matching entries under `images` in a values file.

To retain one local build without a registry, archive its images and record the archive checksum. Verify that checksum before loading the archive on a machine with the same image architecture.

```sh
docker image save -o relayna-reference.tar \
  relayna-local/gateway:reference relayna-local/sdk:reference relayna-local/spooltail:reference \
  relayna-local/studio-backend:reference relayna-local/studio:reference
shasum -a 256 relayna-reference.tar
docker image load -i relayna-reference.tar
```

For a previously downloaded wheel or launcher, keep the component's version, expected SHA-256, and target OS/architecture together. Compare the checksum before installation. Set `SPOOLTAIL_BROKER_VERSION` in Compose or `broker.version` in Helm to match the selected broker. The capability flags advertised by the service describe which routes are enabled.
