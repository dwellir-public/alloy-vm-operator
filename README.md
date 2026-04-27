# alloy-vm

Supported bases:

- Ubuntu 22.04 amd64
- Ubuntu 24.04 amd64

## Metrics scraping to Mimir

Alloy now supports:

- local self metrics and `prometheus.exporter.unix` host metrics
- relation-driven scrape targets over `metrics-endpoint` (`prometheus_scrape`)
- manual scrape targets over `manual-metrics-jobs`
- forwarding metrics upstream over `send-remote-write` (`prometheus_remote_write`)

The intended release-1 operating model is one Alloy unit doing the scraping and forwarding work.
For the shared observability deployment, Alloy forwards into one shared Mimir
metrics store. Tenant-aware relation metadata is not required or published by
`alloy-vm`; separation is done through metric labels such as Juju topology.

### No-upstream behavior

If Alloy has related or manual scrape targets but no `send-remote-write` relation:

- relation-derived and manual scrape jobs are not enabled
- no `prometheus.remote_write` component is rendered
- local self metrics are dropped rather than buffered
- the unit goes to:
  - `waiting: Waiting for remote write before enabling manual or related metrics scraping`

This is deliberate. The charm avoids accumulating local storage pressure when there is nowhere valid to send scraped metrics.

### Remote write outage buffering

When a valid remote write endpoint exists, Alloy renders a bounded WAL buffer on the
`prometheus.remote_write` component:

- `max_keepalive_time = "30m"`

This means:

- short upstream outages are buffered
- longer outages can still lose older samples once the `30m` keepalive window is exceeded

### Example relation to Mimir

Relate Alloy to a deployed shared metrics endpoint such as `mimir-vm` directly:

```bash
juju relate alloy-vm:send-remote-write mimir-vm:receive-remote-write
```

If `mimir-gateway-vm` is deployed as the stable ingress/load balancer in front
of Mimir, relate Alloy to the gateway instead:

```bash
juju relate alloy-vm:send-remote-write mimir-gateway-vm:receive-remote-write
```

In both cases, Alloy consumes a plain `prometheus_remote_write` URL contract.
It does not require `tenant-id`, `X-Scope-OrgID`, or tenant-specific path
handling.

Verify the rendered remote write config on the Alloy unit:

```bash
juju ssh alloy-vm/<unit> 'grep -n "prometheus.remote_write \\\"metrics\\\"" -A8 /etc/alloy/config.alloy'
```

Verify a local unix-exporter metric in Mimir:

```bash
juju ssh mimir-vm/<unit> "curl -fsS 'http://127.0.0.1:9009/prometheus/api/v1/query?query=node_uname_info{juju_application=\"alloy-vm\"}'"
```

### Add a manual metrics scrape job

You can configure Alloy to scrape extra metrics targets that are not provided
over a Juju relation.

Prerequisite: Alloy only enables manual metrics jobs when it has an upstream
remote-write destination.

Relate Alloy to Mimir first:

```bash
juju relate alloy-vm:send-remote-write mimir-vm:receive-remote-write
```

Create a YAML file describing the extra scrape job:

```yaml
# manual-metrics-jobs.yaml
- name: external-node-exporter
  targets:
    - "10.20.30.40:9100"
  metrics_path: /metrics
  scheme: http
  scrape_interval: 30s
  scrape_timeout: 10s
  insecure_skip_verify: false
  labels:
    env: prod
    role: node
```

Apply it to the charm:

```bash
juju config alloy-vm manual-metrics-jobs="$(cat manual-metrics-jobs.yaml)"
```

What Alloy will do:

- render a dedicated `prometheus.scrape` component for `external-node-exporter`
- inject local Juju topology labels such as `juju_model`, `juju_application`, and `juju_unit`
- forward the scraped samples through the existing `prometheus.remote_write` path

Verify the rendered config on the Alloy unit:

```bash
juju ssh alloy-vm/0 'grep -n "prometheus.scrape \"external_node_exporter\"" -A20 /etc/alloy/config.alloy'
```

Verify the job label in Mimir:

```bash
juju ssh mimir-vm/0 "curl -fsS 'http://127.0.0.1:9009/prometheus/api/v1/query?query=up{job=\"external-node-exporter\"}'"
```

To remove the manual job again:

```bash
juju config alloy-vm manual-metrics-jobs=""
```

Notes:

- `name` and `targets` are required.
- `scheme` must be `http` or `https`.
- Release 1 does not support auth, client certificates, relabeling, or service discovery in manual jobs.
- If `send-remote-write` is not related, Alloy will not enable the manual job and the unit will wait for remote write.

## Reconfigure Alloy with full override

Dump the current override value:

```bash
juju config alloy-vm config-override
```

Set a full config override from a file:

```bash
juju config alloy-vm config-override="$(cat alloy.conf)"
```

Clear override and return to charm-generated config:

```bash
juju config alloy-vm config-override=""
```

## Enable live debugging

Enable live debugging mode:

```bash
juju config alloy-vm alloy-livedebugging=true
```

What this does:
- Sets `CUSTOM_ARGS` to `--server.http.listen-addr=0.0.0.0:12345` in `/etc/default/alloy`.
- Adds a `livedebugging { enabled = true }` block to generated Alloy config.

Verify on the unit:

```bash
juju ssh alloy-vm/<unit> 'grep "^CUSTOM_ARGS=" /etc/default/alloy'
juju ssh alloy-vm/<unit> 'grep -n "livedebugging" /etc/alloy/config.alloy'
```

Disable live debugging and restore previous args:

```bash
juju config alloy-vm alloy-livedebugging=false
```

Optional: set your baseline custom args that should be restored after disabling:

```bash
juju config alloy-vm custom_args="--server.http.listen-addr=0.0.0.0:6987"
```

## Enable syslog receivers (phase 6)

Enable TCP+UDP syslog listeners on port `1514`:

```bash
juju config alloy-vm enable-syslogreceivers=true
```

Behavior:
- Starts `loki.source.syslog` listeners for both TCP and UDP on `:1514`.
- Applies relabel rules for remote syslog metadata.
- Routes remote syslog through a dedicated `loki.process "remote_syslog"` stage before
  `loki.write.main`.
- Does not add Juju topology labels to remote syslog.
- If there is no `send-loki-logs` relation, syslog is dropped (`forward_to = []`).

### Optional remote syslog drop controls

The remote syslog pipeline can be reduced before forwarding to Loki.

Charm config:

- `syslog-drop-access-logs`
- `syslog-drop-expressions`
- `syslog-rate-limit`
- `syslog-rate-burst`

Behavior:

- `syslog-drop-access-logs=true` drops common request-style access-log lines such as
  `GET ... HTTP/...` before they reach Loki.
- `syslog-drop-expressions` accepts newline-separated regular expressions for additional
  remote-syslog-only drops.
- `syslog-rate-limit` and `syslog-rate-burst` apply a `stage.limit` guard to the
  surviving remote syslog stream.

This filtering only affects logs received through `loki.source.syslog "receiver"`.
Local journal scraping is unaffected.

Recommended operator use:

- start by suppressing noisy logs at the source charm when possible
- use Alloy as the second guardrail when a related charm still emits too much remote syslog
- keep Loki retention finite as the final guardrail

Recommended starting profiles:

- keep all remote syslog:
  - leave all `syslog-*` drop controls at their defaults
- drop common HAProxy-style access logs but keep service/application logs:
  - `syslog-drop-access-logs=true`
- drop known noisy custom patterns as well:
  - set `syslog-drop-expressions` to newline-separated regular expressions
- rate-limit any surviving bursty remote syslog:
  - set both `syslog-rate-limit` and `syslog-rate-burst`

Example: drop common access logs

```bash
juju config alloy-vm syslog-drop-access-logs=true
```

Example: drop access logs and add a burst guard

```bash
juju config alloy-vm syslog-rate-limit=25
juju config alloy-vm syslog-rate-burst=100
```

Example: add custom drop expressions

```bash
juju config alloy-vm syslog-drop-expressions=$'healthcheck\\n/readiness\\n/livez'
```

This means:

- request-style access logs are removed before they reach Loki
- only `25` surviving remote-syslog lines per second are kept
- short bursts up to `100` lines are allowed before excess lines are dropped

This is useful when a related charm can generate enough access traffic to
inflate Loki ingestion and object-storage growth.

This is intended as a second guardrail. Prefer suppressing noisy logs at the source
charm first when possible.

Reset to default behavior:

```bash
juju config alloy-vm syslog-drop-access-logs=false
juju config alloy-vm syslog-drop-expressions=''
juju config alloy-vm syslog-rate-limit=0
juju config alloy-vm syslog-rate-burst=0
```

Verify on the unit:

```bash
juju ssh alloy-vm/<unit> 'grep -n "loki.process \"remote_syslog\"" -A20 /etc/alloy/config.alloy'
```

Verify in Loki/Grafana:

- send a known manual syslog message and confirm it still appears
- trigger the noisy traffic pattern, for example HAProxy access requests
- confirm the access-log lines no longer appear while the manual message still does

### `syslog-receiver` relation contract

When another charm relates to `alloy-vm:syslog-receiver`, Alloy now publishes the
receiver details over relation data.

Published keys:
- `address`
- `port`
- `protocols`
- `recommended-protocol`
- `ready`
- `reason`

Release-1 behavior:
- `port` is `1514`
- `protocols` is `tcp,udp`
- `recommended-protocol` is `tcp`
- `ready=true` only when syslog receivers are enabled and Alloy has an active
  `send-loki-logs` path to Loki
- if Alloy would drop the received logs because no Loki relation exists, it
  publishes `ready=false` and `reason=waiting for send-loki-logs relation`

This makes Alloy suitable as the immediate syslog receiver for charms such as
`haproxy-dataplane-api`, while `loki-vm` remains the downstream log store.

Verify on the unit:

```bash
juju ssh alloy-vm/<unit> 'grep -n "loki.source.syslog \\\"receiver\\\"" /etc/alloy/config.alloy'
juju ssh alloy-vm/<unit> 'grep -n "protocol = \\\"udp\\\"\\|protocol = \\\"tcp\\\"" /etc/alloy/config.alloy'
```

Disable syslog listeners:

```bash
juju config alloy-vm enable-syslogreceivers=false
```

## Capture host kernel and raw journal matches

The default host log path in `alloy-vm` only follows the named systemd units
from `systemd-units`. You can extend this with:

- `journal-kernel`
- `journal-match-expressions`

These options create an additional host journal source in Alloy.

Behavior:

- `systemd-units` logs still flow through the Juju-labeled log pipeline
- kernel/raw host journal logs do not get `juju_*` labels
- if there is no `send-loki-logs` relation, the broader host journal path is
  dropped just like the existing log pipeline

### `journal-kernel`

Enable host kernel transport logs:

```bash
juju config alloy-vm journal-kernel=true
```

What happens:

- Alloy adds `_TRANSPORT=kernel` to a broader host journal source
- this is intended to capture kernel log traffic similar to `dmesg`
- these entries are forwarded to Loki without Juju topology labels

### `journal-match-expressions`

Add raw journald match expressions as newline-separated clauses:

```bash
juju config alloy-vm journal-match-expressions=$'SYSLOG_IDENTIFIER=lxd'
```

What happens:

- the expressions are passed through without validation
- empty lines are ignored
- invalid expressions can break the rendered Alloy config and cause config
  validation to fail

Example: capture both kernel transport logs and host LXD messages

```bash
juju config alloy-vm journal-match-expressions=$'_TRANSPORT=kernel\nSYSLOG_IDENTIFIER=lxd'
```

Example: keep unit-based logs and add kernel capture

```bash
juju config alloy-vm systemd-units='ssh.service,snap.lxd.daemon.service'
juju config alloy-vm journal-kernel=true
```

What happens:

- `ssh.service` and `snap.lxd.daemon.service` logs still keep Juju topology labels
- kernel transport logs are collected through the separate host journal path
- the host journal path remains unlabeled by Juju topology

Example: monitor an LXD host for kernel, LXD, and audit-style host messages

```bash
juju config alloy-vm systemd-units='snap.lxd.daemon.service'
juju config alloy-vm journal-kernel=true
juju config alloy-vm journal-match-expressions=$'SYSLOG_IDENTIFIER=audit\nSYSLOG_IDENTIFIER=lxd'
```

What happens:

- `snap.lxd.daemon.service` logs are collected through the Juju-labeled service path
- kernel transport logs are collected through the broader host journal path
- host messages emitted with `SYSLOG_IDENTIFIER=lxd` or `SYSLOG_IDENTIFIER=audit`
  are collected through the broader host journal path
- the broader host journal path does not add `juju_*` labels, so these entries
  should be treated as host-scoped logs rather than Juju workload-scoped logs

### Verification

Inspect the rendered host journal blocks:

```bash
juju ssh alloy-vm/<unit> 'grep -n "loki.source.journal" -A6 /etc/alloy/config.alloy'
```

Check that `_TRANSPORT=kernel` or your raw match expression is present:

```bash
juju ssh alloy-vm/<unit> 'grep -n "_TRANSPORT=kernel\\|SYSLOG_IDENTIFIER=lxd" /etc/alloy/config.alloy'
```

Query recent logs in Loki or Grafana after triggering a known matching message:

```bash
juju ssh loki-vm/<unit> 'start=$(date -u -d "15 minutes ago" +%s%N); end=$(date -u +%s%N); curl -sG --data-urlencode "query={}" --data-urlencode limit=20 --data-urlencode start=$start --data-urlencode end=$end http://127.0.0.1:3100/loki/api/v1/query_range'
```

If you added a raw identifier match, trigger the matching host service and look for
the expected log line content in Grafana Explore or through Loki's API.

```bash
juju ssh alloy-vm/<unit> 'journalctl -n 20 --no-pager -u snap.lxd.daemon.service'
```
