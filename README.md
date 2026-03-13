# alloy

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
