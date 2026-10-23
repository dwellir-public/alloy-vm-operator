# Build, Test, and Deploy

This document captures the local verification workflow for the
`machine-observability` v2/v3 consumer support in `alloy-vm`.

## Goals

- accept v2 `machine_observability` payloads from related principals
- accept and forward bounded v3 alert-rule artifacts
- preserve provider Juju topology for multiple principals on the same machine
- render per-source metrics and log pipelines into one Alloy config
- keep existing Grafana Cloud, syslog, and host collection behavior intact

## Local Verification

Run the repo checks first:

```bash
cd /home/erik/dwellir-public/alloy-vm-operator
tox -e lint,static,unit
```

These are required local gates; they do not prove a live Juju deployment.

## Build

Build the 24.04 charm artifact:

```bash
cd /home/erik/dwellir-public/alloy-vm-operator
charmcraft pack --platform ubuntu@24.04:amd64
```

Expected artifact:

```bash
alloy-vm_ubuntu@24.04-amd64.charm
```

## Deploy Shape

`alloy-vm` is the principal machine collector in this design.

Example relation shape:

```bash
juju deploy ./alloy-vm_ubuntu@24.04-amd64.charm
juju relate alloy-vm:machine-observability op-node:machine-observability
juju relate alloy-vm:machine-observability op-reth:machine-observability
juju relate alloy-vm:send-remote-write mimir-vm:receive-remote-write
juju relate alloy-vm:send-loki-logs loki-vm:loki_push_api
```

## Validate Relation Contract

Inspect the relation data published by related principals:

```bash
juju show-unit alloy-vm/0
```

Expected under `machine-observability` relations:

- `schema_version: 2`
- or `schema_version: 3` with Prometheus/Loki artifacts
- `source_topology.application` matches the remote principal app
- `source_topology.unit` matches the remote principal unit

If a provider still publishes v1 payloads, `alloy-vm` blocks intentionally.

## Validate Rendered Alloy Config

Inspect the rendered config:

```bash
juju ssh alloy-vm/0 'sudo sed -n "1,320p" /etc/alloy/config.alloy'
```

Expected:

- per-principal `prometheus.scrape` jobs using provider topology labels
- per-principal `loki.process` blocks using provider topology labels
- journald and file-log inputs forwarding into those per-principal processors
- one shared `prometheus.remote_write`
- one shared `loki.write`

## Validate v3 rules

For a v3 reference provider, verify checksums and artifact types in relation
data. Packaged `cos-tool` validates PromQL/LogQL internally; it does not run as
a service.

```bash
juju relate alloy-vm:send-loki-logs loki-loadbalancer-vm:loki_push_api
juju relate loki-loadbalancer-vm:loki-alert-rules loki-vm:loki_push_api
juju relate loki-loadbalancer-vm:ingress loki-vm:ingress
juju relate alloy-vm:send-remote-write mimir-gateway-vm:receive-remote-write
juju relate mimir-gateway-vm:mimir-alert-rules mimir-vm:receive-remote-write
juju relate mimir-gateway-vm:backend mimir-vm:backend
```

Exercise add/update, valid omission, relation removal, a bad checksum, and an
invalid expression. Malformed, future-version, or structurally invalid outer
data retains the whole relation LKG. Within a valid v3 payload, only a
malformed artifact retains its previous LKG; unrelated rules and telemetry
continue. The provider payload may be exactly `60 * 1024` bytes, rejects only
larger values, and is not chunked.

Upgrade in order: reference library, both Alloy variants, both gateways, then
Grafana VM. Wait for relation convergence between steps.
