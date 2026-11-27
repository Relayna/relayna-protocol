# Reproduce the Protocol compatibility stack

For the current installation and profiles, start with [Relayna Deploy](https://github.com/Relayna/relayna-deploy). [Relayna Examples](https://github.com/Relayna/relayna-examples) supplies task clients and worker integrations. The steps below run the compatibility fixture kept with Protocol.

Use Docker with Buildx and Compose v2, Python 3.12 or later, and Git with access to the component repositories. The stack runs one instance of each component. Gateway and Studio listen on loopback. SDK, Spooltail, Postgres, and Redis are reached over the Compose network.

## Start with Compose

Run from the protocol repository root.

```sh
python3 deploy/build.py --init-env
python3 deploy/build.py
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile studio up -d --wait --wait-timeout 180
python3 deploy/smoke.py
```

The build reads exact Git revisions from `deploy/sources.json`. It uses the standalone SDK repository for the reference service and the standalone Studio repository for both Studio images. The smoke registers the SDK with Gateway and Studio using their existing operator APIs, creates a key restricted to the reference service, and stores it in `deploy/.local/reference.key` with mode `0600`. It submits an echo task through Gateway, retries it, checks replay and cancellation responses, and compares the terminal result in Studio. The output contains the task ID.

Open [local Studio](http://127.0.0.1:18382) and use the operator token from `deploy/.env` to sign in. Look up the emitted task ID in the `reference` service. [Gateway proxy](http://127.0.0.1:18380) serves the registered task routes, and [Gateway control](http://127.0.0.1:18381) exposes the local operator API.

To run the reference submission by hand after registration, use a fresh task ID for each independent run.

```sh
base=http://127.0.0.1:18380/services/reference
key=$(cat deploy/.local/reference.key)
task_id="demo-$(date +%s)"
curl -fsS "$base/tasks" -H "Authorization: Bearer $key" -H 'Content-Type: application/json' \
  -d "{\"task_id\":\"$task_id\",\"payload\":{\"number\":21}}"
curl -fsSN "$base/events/$task_id" -H "Authorization: Bearer $key"
curl -fsS "$base/status/$task_id" -H "Authorization: Bearer $key"
curl -fsS "$base/tasks/$task_id/usage" -H "Authorization: Bearer $key"
unset key
```

For the core stack, omit `--profile studio`. Postgres and Spooltail data persist across `docker compose down` and subsequent starts. Studio can optionally show Gateway request observations when `RELAYNA_STUDIO_GATEWAY_USAGE_TOKEN` is set in `deploy/.env` to a scoped Gateway operator token with `usage:read`. The reference registration is already mapped to the Studio service ID.

## Install the Helm chart locally

Use a local single-node Kubernetes cluster with a default StorageClass. Build the same container images and make them available to the node. For kind, load the images into the named local cluster. From a private registry, set the `images` entries in a Helm values override and configure `imagePullSecrets`.

```sh
kind load docker-image --name relayna-dev \
  relayna-local/gateway:reference relayna-local/sdk:reference relayna-local/spooltail:reference \
  relayna-local/studio-backend:reference relayna-local/studio:reference

kubectl --context kind-relayna-dev create namespace relayna-dev
sed -n \
  -e 's/^RELAYNA_DB_PASSWORD=/db-password=/p' \
  -e 's/^RELAYNA_GATEWAY_ADMIN_TOKEN=/gateway-admin-token=/p' \
  -e 's/^RELAYNA_STUDIO_OPERATOR_TOKEN=/studio-operator-token=/p' \
  -e 's/^RELAYNA_STUDIO_GATEWAY_USAGE_TOKEN=/studio-gateway-usage-token=/p' deploy/.env |
  kubectl --context kind-relayna-dev -n relayna-dev create secret generic relayna-local --from-env-file=/dev/stdin

helm upgrade --install relayna deploy/helm/relayna --kube-context kind-relayna-dev \
  -n relayna-dev --wait --timeout 5m
```

The chart uses ClusterIP Services and PVCs for Postgres and Spooltail. Use local port forwards to exercise it. Stop Compose first or use separate local ports and pass them to the smoke script.

```sh
kubectl --context kind-relayna-dev -n relayna-dev port-forward svc/relayna-gateway 18380:8080 18381:8081
# In another shell
kubectl --context kind-relayna-dev -n relayna-dev port-forward svc/relayna-studio 18382:80
# In a third shell, use a separate key file for the Kubernetes Gateway database
python3 deploy/smoke.py --sdk-upstream http://relayna-sdk:8080 --key-file deploy/.local/helm-reference.key
```

Set `studio.enabled=false` to package the core stack. For render-only validation use `helm lint deploy/helm/relayna` and `helm template relayna deploy/helm/relayna`.

## Opt in to standalone Cell

The reference containers run the native echo handler. For gVisor execution, install Cell using its Linux or VM setup on the trusted host and build the reference OCI image from the pinned Cell source. Run the SDK reference process on that host with the configured Cell launcher and Unix socket. Keep the SDK API privately reachable from Gateway and Studio and point both service registrations to it. Use the same Spooltail topics, worker group, and cancellation topic as the service you replace. Stop the native SDK worker before switching the handler.

The host process reads `RELAYNA_CELL_TASK_LAUNCHER`, `RELAYNA_CELL_IMAGE`, `RELAYNA_CELL_SOCKET`, and `RELAYNA_CELL_TIMEOUT`. For a host SDK, expose the Compose broker gRPC port only on loopback and set `SPOOLTAIL_TARGET` to that address. In Helm, a local port-forward to the Spooltail service provides the same connection. Set the Gateway and Studio SDK upstream to a host address reachable on their trusted deployment network.

From the pinned Cell checkout, run `bash scripts/package-standalone.sh arm64` or `bash scripts/package-standalone.sh amd64` for the Linux archive and checksum. The standalone launcher needs access to the Cell operator socket. For scoped inference, follow Cell's operator setup for `--task-inference-config` and set `RELAYNA_CELL_INFERENCE=on` on the SDK reference process. The launcher sends the task and attempt IDs to Cell and reports its sandbox identity through the SDK's task status. [Cell installation and launcher](https://github.com/Relayna/relayna-cell) · [Python SDK reference](https://github.com/Relayna/relayna-sdk/blob/main/docs/spooltail.md)

The coordinated standalone smoke verified a real Gateway submission, SDK execution through Spooltail and gVisor Cell, and model inference through Gateway against a synthetic LiteLLM-compatible endpoint. The SDK returned the expected result and two trusted Cell inference events. Gateway usage retained the same attempt ID and the expected five synthetic tokens.
