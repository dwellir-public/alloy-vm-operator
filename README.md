# alloy

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
juju relate alloy:send-remote-write mimir-vm:receive-remote-write
```

Verify the rendered remote write config on the Alloy unit:

```bash
juju ssh alloy/<unit> 'grep -n "prometheus.remote_write \\\"metrics\\\"" -A8 /etc/alloy/config.alloy'
```

Verify a local unix-exporter metric in Mimir:

```bash
juju ssh mimir-vm/<unit> "curl -fsS 'http://127.0.0.1:9009/prometheus/api/v1/query?query=node_uname_info{juju_application=\"alloy\"}'"
```

## Reconfigure Alloy with full override

Dump the current override value:

```bash
juju config alloy config-override
```

Set a full config override from a file:

```bash
juju config alloy config-override="$(cat alloy.conf)"
```

Clear override and return to charm-generated config:

```bash
juju config alloy config-override=""
```

## Enable live debugging

Enable live debugging mode:

```bash
juju config alloy alloy-livedebugging=true
```

What this does:
- Sets `CUSTOM_ARGS` to `--server.http.listen-addr=0.0.0.0:12345` in `/etc/default/alloy`.
- Adds a `livedebugging { enabled = true }` block to generated Alloy config.

Verify on the unit:

```bash
juju ssh alloy/<unit> 'grep "^CUSTOM_ARGS=" /etc/default/alloy'
juju ssh alloy/<unit> 'grep -n "livedebugging" /etc/alloy/config.alloy'
```

Disable live debugging and restore previous args:

```bash
juju config alloy alloy-livedebugging=false
```

Optional: set your baseline custom args that should be restored after disabling:

```bash
juju config alloy custom_args="--server.http.listen-addr=0.0.0.0:6987"
```

## Enable syslog receivers (phase 6)

Enable TCP+UDP syslog listeners on port `1514`:

```bash
juju config alloy enable-syslogreceivers=true
```

Behavior:
- Starts `loki.source.syslog` listeners for both TCP and UDP on `:1514`.
- Applies relabel rules for remote syslog metadata.
- Forwards syslog directly to `loki.write.main` (no Juju topology processor on syslog flow).
- If there is no `send-loki-logs` relation, syslog is dropped (`forward_to = []`).

Verify on the unit:

```bash
juju ssh alloy/<unit> 'grep -n "loki.source.syslog \\\"receiver\\\"" /etc/alloy/config.alloy'
juju ssh alloy/<unit> 'grep -n "protocol = \\\"udp\\\"\\|protocol = \\\"tcp\\\"" /etc/alloy/config.alloy'
```

Disable syslog listeners:

```bash
juju config alloy enable-syslogreceivers=false
```
