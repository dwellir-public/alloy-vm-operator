# Build, Test, and Deploy

This document captures the local verification workflow for the
`machine-observability` v2 consumer support in `alloy-vm`.

## Goals

- accept v2 `machine_observability` payloads from related principals
- preserve provider Juju topology for multiple principals on the same machine
- render per-source metrics and log pipelines into one Alloy config
- keep existing Grafana Cloud, syslog, and host collection behavior intact

## Local Verification

Run the repo checks first:

```bash
cd /home/erik/dwellir-public/alloy-vm-operator
tox -e lint,static,unit
```

Validated result for this change:

- `tox -e unit` passed
- `tox -e static` should stay clean
- `tox -e lint` should stay clean

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
juju relate alloy-vm:send-loki-logs loki-loadbalancer-vm:loki_push_api
```

## Validate Relation Contract

Inspect the relation data published by related principals:

```bash
juju show-unit alloy-vm/0
```

Expected under `machine-observability` relations:

- `schema_version: 2`
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
