# Enable Host Metrics Option Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put `alloy-vm`'s host-metrics collection behind an `enable-host-metrics` boolean config option defaulting to `false`, matching `alloy-sub`.

**Architecture:** `ConfigBuilder` gains a `host_metrics_enabled` flag that gates the whole host-metrics group — the `prometheus.exporter.unix` component, its `discovery.relabel`, its `prometheus.scrape`, and every per-workload `prometheus.relabel` copy. Alloy's own self-metrics pipeline (`alloy_self`) is unaffected and always renders. The charm reads the new config option and passes it through; nothing else in the charm changes.

**Tech Stack:** Python 3.10+, `ops` 3.x machine charm, `ops.testing` (Harness + Scenario), pytest, ruff, pyright, tox with `uv`.

## Background

`alloy-vm` collects host metrics in-process through Alloy's built-in
`prometheus.exporter.unix "default"`, scraped as the `alloy-local` job and — since
the per-unit-copies work — duplicated to one `prometheus.relabel` component per
related workload. That collection is unconditional today.

`alloy-sub` gained the same capability behind an `enable-host-metrics` boolean
defaulting to `false` (see `/home/ibrahim/Projects/alloy-sub-operator`,
`charmcraft.yaml` and `src/charm.py`). This plan gives `alloy-vm` the same knob
with the same name, default, and meaning.

Decisions already made with the repo owner, do not relitigate them:

- **The default is `false`,** matching `alloy-sub`, and the owner has explicitly
  accepted the consequence: existing `alloy-vm` deployments stop collecting host
  metrics when they refresh onto this revision, until an operator sets the option
  to `true`. This is a deliberate behaviour change, not an oversight — see Risks.
- **`false` removes the entire host-metrics pipeline:** no exporter component, no
  `host_metrics` relabel, no `host_metrics` scrape, no per-workload copies. No
  collectors run at all.
- **Alloy's own self-metrics keep rendering** in both states. The `alloy_self`
  relabel and scrape describe the collector's health, not the machine's, and are
  not what this option is about.

## Global Constraints

- Python `>=3.10`. `src/config_builder.py` already uses `from __future__ import annotations`.
- ruff `line-length = 99`; lint rules `E, W, F, C, N, D, I001` — lint must be
  clean, including import ordering. Docstrings required on public functions. Test
  files are exempt from `D100`–`D104`.
- mccabe `max-complexity = 10`.
- Charm source lives in `src/` and is imported flat (`from config_builder import
  ...`), never as `src.config_builder`.
- The option is named exactly `enable-host-metrics`, type `boolean`, default
  `false` — the same three values `alloy-sub` uses.
- Commit messages use the repo's conventional prefixes (`feat:`, `fix:`,
  `docs:`, `refactor:`) and end with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Full verification before each commit: `uv run tox -e lint`,
  `uv run tox -e static`, `uv run tox -e unit`. The suite is 75 tests passing at
  the branch point, with 6 pre-existing `ops` Harness deprecation warnings that
  are baseline noise.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/config_builder.py` | New `host_metrics_enabled` input; gate the exporter, relabel, scrape, and copies on it. |
| `charmcraft.yaml` | Declare `enable-host-metrics`. |
| `src/charm.py` | Read the option and pass it to `ConfigBuilder`. |
| `README.md` | Document the option in the host-metric attribution section. |
| `docs/charm-architecture.md` | Note that host metrics are opt-in. |
| `tests/unit/test_config_builder.py` | Rendering tests for the disabled path. |
| `tests/unit/test_charm.py` | Charm-level: the option gates the pipeline. One existing test opts in. |
| `tests/unit/test_machine_observability_relation.py` | Two existing tests opt in. |

---

### Task 1: Gate the host-metrics pipeline in ConfigBuilder

**Files:**
- Modify: `src/config_builder.py` — the `__init__` signature and assignments (around `:88-130`), `_render_base_blocks` (`:169-187`), `_active_host_metrics_copies` (`:294-304`)
- Test: `tests/unit/test_config_builder.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ConfigBuilder(..., host_metrics_enabled: bool = True)`, keyword-only.
  When `False`, `build()` renders no `prometheus.exporter.unix`, no
  `discovery.relabel "host_metrics"`, no `prometheus.scrape "host_metrics"`, and
  no `prometheus.relabel` copies. Task 2 passes the charm's config value.

**Why the builder's default is `True` when the charm option's default is `false`:**
the builder is a renderer that draws what it is told to draw, and every existing
test constructs one directly and expects today's output. The opt-in lives in
exactly one place — the charm config option Task 2 adds — and the charm always
passes the value explicitly. Do not "fix" this by flipping the builder default;
that would silently rewrite the expectations of roughly thirty existing tests.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config_builder.py`:

```python
def test_disabled_host_metrics_render_no_exporter_relabel_or_scrape():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_enabled=False,
    ).build()

    assert "prometheus.exporter.unix" not in rendered
    assert 'discovery.relabel "host_metrics" {' not in rendered
    assert 'prometheus.scrape "host_metrics" {' not in rendered


def test_disabled_host_metrics_still_render_alloys_own_metrics():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_enabled=False,
    ).build()

    alloy_self = rendered.split('discovery.relabel "alloy_self" {', 1)[1].split("\n}", 1)[0]

    assert '    job         = "alloy",' in alloy_self
    assert 'prometheus.scrape "alloy_self" {' in rendered
    assert "  forward_to      = [prometheus.remote_write.metrics.receiver]" in rendered


def test_disabled_host_metrics_render_no_copies():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[OP_NODE_COPY, OP_RETH_COPY],
        host_metrics_enabled=False,
    ).build()

    assert "prometheus.relabel" not in rendered


def test_enabled_host_metrics_are_the_builder_default():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[OP_NODE_COPY],
    ).build()

    assert 'prometheus.exporter.unix "default" {' in rendered
    assert 'prometheus.scrape "host_metrics" {' in rendered
    assert 'prometheus.relabel "op_node_0" {' in rendered
```

`OP_NODE_COPY` and `OP_RETH_COPY` are module-level fixtures already defined in
that file — reuse them, do not redefine them.

The last test is the guard on the rationale above: it pins that a builder given no
`host_metrics_enabled` argument still renders the full pipeline, so a future edit
cannot flip the default without a test telling it so.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config_builder.py -v`
Expected: the three `disabled` tests FAIL with
`TypeError: __init__() got an unexpected keyword argument 'host_metrics_enabled'`.
`test_enabled_host_metrics_are_the_builder_default` PASSES already — it pins
existing behaviour so the new flag cannot change it silently.

- [ ] **Step 3: Add the parameter**

In `src/config_builder.py`, add the parameter to `ConfigBuilder.__init__`
immediately after `host_metrics_copies`:

```python
        topology_labels: dict[str, str],
        host_metrics_copies: list[HostMetricsCopy] | None = None,
        host_metrics_enabled: bool = True,
        log_source_groups: list[LogSourceGroup] | None = None,
```

And store it next to the existing assignment:

```python
        self._host_metrics_copies = self._deduplicate_host_metrics_copies(
            host_metrics_copies or []
        )
        self._host_metrics_enabled = host_metrics_enabled
        self._log_source_groups = log_source_groups or []
```

- [ ] **Step 4: Gate the rendering**

Replace `_render_base_blocks` with a version that asks two small helpers for the
host-metrics blocks, so the conditional does not bloat the block list:

```python
    def _render_base_blocks(self) -> list[str]:
        blocks = [
            self._render_logging(),
            "",
            *self._render_host_metrics_sources(),
            self._render_alloy_self_relabel(),
            "",
            *([self._render_remote_write(), ""] if self._remote_write_endpoints else []),
            *self._render_host_metrics_scrapes(),
            self._render_alloy_self_scrape(),
        ]
        for copy in self._active_host_metrics_copies():
            blocks.extend(["", self._render_host_metrics_copy(copy)])
        return blocks

    def _render_host_metrics_sources(self) -> list[str]:
        """Return the exporter and its relabel, or nothing when host metrics are off."""
        if not self._host_metrics_enabled:
            return []
        return [self._render_unix_exporter(), "", self._render_host_metrics_relabel(), ""]

    def _render_host_metrics_scrapes(self) -> list[str]:
        """Return the host-metrics scrape, or nothing when host metrics are off."""
        if not self._host_metrics_enabled:
            return []
        return [self._render_host_metrics_scrape(), ""]
```

Note the block order is unchanged when host metrics are on: logging, exporter,
`host_metrics` relabel, `alloy_self` relabel, remote write, `host_metrics`
scrape, `alloy_self` scrape, then the copies.

Then extend the copy filter so a disabled pipeline has no copies to fan out to —
this is what keeps `_host_metrics_forward_to` from naming a component that no
longer renders:

```python
    def _active_host_metrics_copies(self) -> list[HostMetricsCopy]:
        """Return the copies worth rendering.

        A copy needs a pipeline to copy from, somewhere to send its samples --
        which requires a remote-write upstream -- and something to say about them,
        which requires at least one non-empty topology label. Otherwise it renders
        no component and attributes nothing to anyone.
        """
        if not (self._host_metrics_enabled and self._remote_write_endpoints):
            return []
        return [copy for copy in self._host_metrics_copies if any(copy.topology_labels.values())]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_builder.py -v`
Expected: PASS, every test in the file. The existing tests all construct builders
without the new argument, so they exercise the enabled path unchanged.

- [ ] **Step 6: Verify and commit**

```bash
uv run tox -e lint && uv run tox -e static && uv run tox -e unit
git add -A
git commit -m "$(cat <<'EOF'
feat: let callers switch host metrics off

Host-metric collection is about to become opt-in, so the renderer needs
to be able to leave it out entirely: no exporter, no relabel, no scrape,
and no per-workload copies to fan out to.

Alloy's own self-metrics are unaffected. They describe the collector's
health rather than the machine's, and are not what the switch is about.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add the config option and wire it through

**Files:**
- Modify: `charmcraft.yaml` — new `enable-host-metrics` entry in `config.options`
- Modify: `src/charm.py` — new `_host_metrics_enabled()`, passed in `_render_config_text`
- Modify: `README.md`, `docs/charm-architecture.md`
- Test: `tests/unit/test_charm.py`, `tests/unit/test_machine_observability_relation.py`

**Interfaces:**
- Consumes: `ConfigBuilder(..., host_metrics_enabled=...)` from Task 1.
- Produces: nothing later tasks rely on. This is the last task.

Three existing charm-level tests assert on host-metric rendering. They will fail
once the default takes effect, because the charm will stop rendering the pipeline
unless the test opts in. Step 5 opts each of them in — that is the correct fix,
not weakening the assertions, because each test is about something else
(remote-write gating, per-workload copies) and merely needs host metrics present
to say it.

- [ ] **Step 1: Declare the option**

In `charmcraft.yaml`, add this entry to `config.options`, immediately before
`manual-metrics-jobs`:

```yaml
    enable-host-metrics:
      description: |
        Collect host-level metrics using Alloy's built-in node_exporter
        (prometheus.exporter.unix). No process is installed on the machine and no
        port is opened; the exporter runs inside Alloy and its metrics are
        forwarded over remote write. This job scrapes every 15s.

        Each workload related over `machine-observability` also receives its own
        copy of these metrics under its own Juju topology, so a machine shared by
        two workloads reports three sets: one per workload and one for the
        alloy-vm unit.

        Default collectors emit roughly a thousand series per host, so leave this
        off where remote write volume is a concern. Alloy's own metrics are
        collected either way.
      type: boolean
      default: false
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_charm.py`:

```python
def test_host_metrics_are_off_by_default(monkeypatch):
    seen: dict[str, str] = {}
    ctx = testing.Context(AlloyCharm)
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr(
        "charm.alloy.write_config_text",
        lambda config_text, **_: seen.__setitem__("config", config_text),
    )

    ctx.run(ctx.on.config_changed(), testing.State())

    assert "prometheus.exporter.unix" not in seen["config"]
    assert 'prometheus.scrape "host_metrics" {' not in seen["config"]
    assert 'prometheus.scrape "alloy_self" {' in seen["config"]


def test_enabling_host_metrics_renders_the_pipeline(monkeypatch):
    seen: dict[str, str] = {}
    ctx = testing.Context(AlloyCharm)
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr(
        "charm.alloy.write_config_text",
        lambda config_text, **_: seen.__setitem__("config", config_text),
    )

    ctx.run(
        ctx.on.config_changed(),
        testing.State(config={"enable-host-metrics": True}),
    )

    assert 'prometheus.exporter.unix "default" {' in seen["config"]
    assert 'prometheus.scrape "host_metrics" {' in seen["config"]
```

Match the monkeypatch style already used by the other `testing.Context` tests in
that file; if a nearby test patches something these two also need, patch it the
same way rather than inventing a new harness shape.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_charm.py -v`
Expected: `test_host_metrics_are_off_by_default` FAILS (the pipeline still
renders — the option is declared but nothing reads it);
`test_enabling_host_metrics_renders_the_pipeline` PASSES already, since the
pipeline renders unconditionally today.

- [ ] **Step 4: Read the option and pass it through**

In `src/charm.py`, add this method next to the other config readers — put it
directly below `_journal_kernel_enabled`:

```python
    def _host_metrics_enabled(self) -> bool:
        """Return True when host-level metrics should be collected."""
        return bool(self.config.get("enable-host-metrics", False))
```

And pass it in `_render_config_text`, next to the copies argument:

```python
            host_metrics_copies=self._machine_observability_host_metrics_copies(),
            host_metrics_enabled=self._host_metrics_enabled(),
            log_source_groups=self._machine_observability_log_source_groups(),
        )
```

Leave `_machine_observability_host_metrics_copies()` as it is: the builder
decides whether the copies render, and the charm stays a dumb collector of
relation data.

- [ ] **Step 5: Opt the three existing tests in**

Each of these asserts on host-metric rendering and now needs the option set. Add
the config value; change nothing else about them.

- `tests/unit/test_charm.py`, the test whose assertions include
  `'prometheus.scrape "host_metrics" {'` (around `:1015`): its `config` dict
  currently reads

  ```python
      config = {
          "config-override": "",
          "custom_args": DEFAULT_ARGS,
          "alloy-livedebugging": False,
          "enable-syslogreceivers": False,
          "systemd-units": "",
          "log-level": "info",
      }
  ```

  Add `"enable-host-metrics": True,` to it.

- `tests/unit/test_machine_observability_relation.py`,
  `test_every_related_workload_gets_a_host_metrics_copy`: change
  `harness.update_config({"systemd-units": "ssh.service"})` to
  `harness.update_config({"systemd-units": "ssh.service", "enable-host-metrics": True})`.

- `tests/unit/test_machine_observability_relation.py`,
  `test_no_related_workload_renders_no_copies`: same change to its
  `harness.update_config(...)` call. Its point is that no copies render *while
  host metrics are on*, which is only meaningful with the option set.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_charm.py tests/unit/test_machine_observability_relation.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `uv run tox -e unit`
Expected: PASS, 79 tests. If a test outside the three named in Step 5 fails, do
not edit it — report it with the exact assertion, its file:line, and the actual
output.

- [ ] **Step 8: Update the README**

In `README.md`, the `### Host metric attribution` section currently opens with:

    ### Host metric attribution

    `alloy-vm` collects host metrics for the whole machine rather than for itself, so
    every workload related over `machine-observability` receives its own copy of them,
    labelled with that workload's Juju topology:

Replace those three prose lines (keep the heading, and keep the bullet list that
follows untouched) with:

    ### Host metric attribution

    Host metrics are opt-in. Set `enable-host-metrics=true` to collect them:

    ```bash
    juju config alloy-vm enable-host-metrics=true
    ```

    With the option off, which is the default, Alloy runs no host-metric collectors
    at all: no exporter, no host-metric scrape, and no per-workload copies. Alloy's
    own metrics from `127.0.0.1:6987` are collected either way.

    When enabled, `alloy-vm` collects host metrics for the whole machine rather than
    for itself, so every workload related over `machine-observability` receives its
    own copy of them, labelled with that workload's Juju topology:

(The indentation above is this plan's quoting of the file's content — the README
lines themselves start at column zero, and the fenced `bash` block is a real
fence in the README.)

- [ ] **Step 9: Update the architecture doc**

In `docs/charm-architecture.md`, in the "important topology distinction" list,
replace this bullet (currently at `:74-77`):

    - host metrics are collected once and copied per related workload: each copy
      carries that provider's `source_topology`, and the `alloy-vm` unit keeps a copy
      under its own topology; both local scrapes, including Alloy's own metrics, run
      at the same pinned 15s interval as the copies

with:

    - host metrics are opt-in through `enable-host-metrics` and, when enabled, are
      collected once and copied per related workload: each copy carries that
      provider's `source_topology`, and the `alloy-vm` unit keeps a copy under its
      own topology; both local scrapes, including Alloy's own metrics, run at the
      same pinned 15s interval as the copies

Alloy's own metrics are collected whether or not the option is set, so the
trailing clause about both local scrapes stays accurate in both states.

- [ ] **Step 10: Verify and commit**

```bash
uv run tox -e lint && uv run tox -e static && uv run tox -e unit
git add -A
git commit -m "$(cat <<'EOF'
feat: make host metrics opt-in

alloy-sub gates the same collection behind enable-host-metrics, and an
operator configuring both charms should not have to remember that one of
them collects host metrics unasked. The option, its name, and its default
match alloy-sub.

Existing deployments stop collecting host metrics on refresh until the
option is set. That is the deliberate cost of the shared default; the
alternative was a knob that means the opposite thing in each charm.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Manual Validation

Not required to merge — the unit tests cover the rendering — but this is how to
confirm it on a real machine:

```bash
juju config alloy-vm enable-host-metrics=false
juju ssh alloy-vm/0 'grep -c "prometheus.exporter.unix" /etc/alloy/config.alloy'   # expect 0
juju ssh alloy-vm/0 'grep -c "alloy_self" /etc/alloy/config.alloy'                  # expect 2

juju config alloy-vm enable-host-metrics=true
juju ssh alloy-vm/0 'grep -n "prometheus.exporter.unix" -A 4 /etc/alloy/config.alloy'
```

Then confirm host metrics stop and start upstream:

```bash
juju ssh mimir-vm/0 "curl -fsS 'http://127.0.0.1:9009/prometheus/api/v1/query?query=count+by+(juju_unit)+(node_load1)'"
```

## Risks

- **Every existing deployment loses host metrics on refresh.** This is the
  accepted consequence of matching `alloy-sub`'s default, and it is silent: the
  unit stays Active, the config is valid, and only the absence of `node_*` series
  upstream reveals it. Anyone rolling this out should plan to set
  `enable-host-metrics=true` on units that had host metrics before, and should
  expect a gap between the refresh and the config change. A dashboard or alert
  keyed on `node_*` will fire during that window.
- **The gap is invisible from `juju status`.** Nothing in the charm's status
  reports that host metrics are off, because "off" is the documented default
  rather than a fault. If that turns out to matter operationally, the follow-up is
  a status message, not a different default.
- **The builder's default and the charm option's default disagree** — `True` and
  `false` respectively. That is deliberate and documented in Task 1, but it is
  the kind of asymmetry a future reader may try to "fix". The test
  `test_enabled_host_metrics_are_the_builder_default` exists to make that attempt
  fail loudly.
- **The `alloy-local` job disappears when the option is off.** Alloy's own
  self-metrics carry `job="alloy"` via their target map, so with host metrics
  disabled nothing renders under `job="alloy-local"`. Queries filtering on that
  job return nothing rather than fewer series.
