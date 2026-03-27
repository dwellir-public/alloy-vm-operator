# Alloy VM Charm Architecture

## Overview

`alloy-vm-operator` manages a Grafana Alloy machine deployment that can:

- receive scrape targets over `metrics-endpoint`
- forward metrics over `send-remote-write`
- receive logs over `syslog-receiver`
- forward logs over `send-loki-logs`

`src/charm.py` stays orchestration-focused. Alloy service control and file management stay in
[`src/alloy.py`](/home/erik/Loki-project/alloy-vm-operator/src/alloy.py), and Alloy config
assembly stays in
[`src/config_builder.py`](/home/erik/Loki-project/alloy-vm-operator/src/config_builder.py).

## Metrics Flow

Remote scrape providers publish Prometheus-style jobs through the `prometheus_scrape` relation
contract on `metrics-endpoint`. The charm consumes those jobs and translates the supported subset
into Alloy scrape components before wiring them to each active remote-write endpoint.

By default, Alloy preserves the generated Prometheus job name from the scrape relation. A provider
can optionally publish a per-unit `metrics_job_name` relation key, and Alloy will use that value as
the final metric `job` label only for that unit. This is used by `lxd-host` so the metric `job`
matches LXD's direct Loki `instance` label, which makes Grafana dashboard `19131` work without
extra user configuration.

This override is intentionally opt-in:

- providers that do not publish `metrics_job_name` keep their existing generated job names
- Juju topology labels remain unchanged
- future scrape providers are unaffected unless they explicitly adopt the override field

## Logging Flow

For logs, Alloy can either forward its own local journal collection or accept remote syslog traffic
through `syslog-receiver`. The charm publishes receiver readiness and connection details on the
relation, and related machine charms decide whether to enable local forwarding.

## Operational Notes

- Configuration rendering is idempotent and only reapplies when the rendered Alloy config or Alloy
  runtime arguments change.
- Invalid config is validated before apply and preserved separately for debugging.
- Remote-write gated scraping means metric scrape jobs are only rendered when at least one
  `send-remote-write` endpoint is available.
