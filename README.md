# alloy-vm

## Metrics scraping to Mimir

Alloy now supports:

- local self metrics and `prometheus.exporter.unix` host metrics
- relation-driven scrape targets over `metrics-endpoint` (`prometheus_scrape`)
- forwarding metrics upstream over `send-remote-write` (`prometheus_remote_write`)

The intended release-1 operating model is one Alloy unit doing the scraping and forwarding work.

### No-upstream behavior

If Alloy has related scrape targets but no `send-remote-write` relation:

- relation-derived remote scrape jobs are not enabled
- no `prometheus.remote_write` component is rendered
- local self metrics are dropped rather than buffered
- the unit goes to:
  - `waiting: Waiting for remote write before enabling related metrics scraping`

This is deliberate. The charm avoids accumulating local storage pressure when there is nowhere valid to send scraped metrics.

### Remote write outage buffering

When a valid remote write endpoint exists, Alloy renders a bounded WAL buffer on the
`prometheus.remote_write` component:

- `max_keepalive_time = "30m"`

This means:

- short upstream outages are buffered
- longer outages can still lose older samples once the `30m` keepalive window is exceeded

### Example relation to Mimir

Relate Alloy to a deployed `mimir-vm` application:

```bash
juju relate alloy-vm:send-remote-write mimir-vm:receive-remote-write
```

Verify the rendered remote write config on the Alloy unit:

```bash
juju ssh alloy-vm/<unit> 'grep -n "prometheus.remote_write \\\"metrics\\\"" -A8 /etc/alloy/config.alloy'
```

Verify a local unix-exporter metric in Mimir:

```bash
juju ssh mimir-vm/<unit> "curl -fsS 'http://127.0.0.1:9009/prometheus/api/v1/query?query=node_uname_info{juju_application=\"alloy-vm\"}'"
```

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
