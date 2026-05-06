# Alloy VM Charm Architecture

## Overview

`alloy-vm-operator` manages a Grafana Alloy machine deployment that can:

- receive scrape targets over `metrics-endpoint`
- receive manual scrape targets from the `manual-metrics-jobs` config option
- forward metrics over `send-remote-write`
- consume authenticated cloud sink config over `grafana-cloud-config`
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

Remote-write destinations can come from the plain `send-remote-write` relation
or from `grafana-cloud-config`. The Grafana Cloud path extends the sink model
to include per-signal basic auth credentials and an optional CA bundle, so
Alloy can render authenticated `prometheus.remote_write` endpoints while still
keeping the existing relation contract for plain upstream URLs.

Operators can also define manual non-related scrape jobs through the `manual-metrics-jobs` config
option. Those jobs are parsed by [`src/manual_metrics_jobs.py`](/home/erik/dwellir-public/alloy-vm-operator/src/manual_metrics_jobs.py),
translated into the same `MetricsScrapeJob` model as relation-derived jobs, and merged into the
rendered Alloy config only when a `send-remote-write` endpoint exists.

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

For logs, Alloy can either forward its own local journal collection or accept
remote syslog traffic through `syslog-receiver`. Outbound Loki sinks can come
from `send-loki-logs` or `grafana-cloud-config`, and when both are present the
rendered `loki.write` block forwards to both destinations. Grafana Cloud Loki
uses signal-specific credentials from the relation when available.

During `update-status`, the charm probes Grafana Cloud metrics and logs
endpoints and surfaces connectivity failures as a blocked status. Successful
probes clear prior Grafana Cloud connectivity errors on the next status
reconciliation.

## Operational Notes

- Configuration rendering is idempotent and only reapplies when the rendered Alloy config or Alloy
  runtime arguments change.
- Invalid config is validated before apply and preserved separately for debugging.
- Remote-write gated scraping means metric scrape jobs are only rendered when at least one
  `send-remote-write` endpoint is available, whether those jobs came from relations or manual
  operator config.
