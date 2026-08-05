# Host Metrics Per-Unit Copies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every workload related over `machine-observability` its own copy of the machine's host metrics, labelled with that workload's Juju topology, and pin the host-metric scrape at 15s.

**Architecture:** The unix exporter is scraped once. That single scrape fans out in-pipeline to one `prometheus.relabel` component per related workload, each stamping that workload's `source_topology` labels and forwarding to remote write. Alloy's own `127.0.0.1:6987` self-metrics move to their own relabel/scrape pair so the copies carry host metrics only. The `alloy-vm` unit keeps its own host-metric copy exactly as today.

**Tech Stack:** Python 3.10+, `ops` 3.x machine charm, `ops.testing` (Harness + Scenario), pytest, ruff, pyright, tox with `uv`.

## Background

`alloy-vm` already collects host metrics in-process through Alloy's built-in
`prometheus.exporter.unix "default"`, relabeled by `discovery.relabel
"local_metrics"` and scraped as the `alloy-local` job. Today those metrics carry
the collector's own Juju topology, which is wrong on a machine shared by several
workloads: the metrics describe the machine, not the collector.

An earlier attempt labelled them with a single comma-joined `juju_unit`
(`"alloy-vm/0,op-node/0,op-reth/2"`). That was rejected by the consumer: a joined
label cannot be selected with an equality matcher and cannot join to workload
series. This plan takes the other route — a full copy per unit — and the branch
was rebased so the joined-label attempt is not in its history.

Decisions already made with the repo owner, do not relitigate them:

- Each copy carries the **workload's full topology** — `juju_model`,
  `juju_model_uuid`, `juju_application`, `juju_unit`, `juju_charm` from that
  payload's `source_topology` — so host metrics join with that workload's own
  metrics.
- The `alloy-vm` unit **always** keeps its own copy. A machine with two related
  workloads emits three sets of host metrics.
- Duplication happens **in-pipeline**: one scrape of the exporter, fanned out to
  one `prometheus.relabel` per unit. The exporter's collectors run once per
  interval regardless of how many units share the machine.
- Every copy keeps `job = "alloy-local"`. Copies are distinguished by their
  `juju_*` labels alone.
- The host-metric scrape is pinned at 15s. It currently renders no
  `scrape_interval` at all and therefore inherits Alloy's one-minute default.
- The unit list comes from `machine-observability` payloads only — not from
  `metrics-endpoint`, `syslog-receiver`, or `relation.units`.

### One judgment call the owner has not ruled on

The single `discovery.relabel "local_metrics"` component feeds two different
things: the unix exporter's targets and Alloy's own `127.0.0.1:6987` target.
Fanning out from the existing scrape would therefore duplicate Alloy's `alloy_*`
self-metrics into every workload copy, which is noise — those series describe the
collector, not the machine.

This plan splits that component in two (`host_metrics` and `alloy_self`) so the
fan-out carries host metrics only. The exporter is still collected exactly once
per interval; the extra scrape is an HTTP GET against Alloy's own endpoint, not a
`/proc` walk. Component names in the rendered config change, which is internal to
Alloy — **no metric label changes for either existing series set**, including the
`job = "alloy"` label on Alloy's self-metrics, which is preserved by keeping the
target map verbatim.

## Global Constraints

- Python `>=3.10`. `src/config_builder.py` and `src/machine_observability_sources.py`
  already use `from __future__ import annotations`.
- ruff `line-length = 99`; lint rules `E, W, F, C, N, D, I001` — lint must be
  clean, including import ordering. Every new class and public function needs a
  docstring. Test files are exempt from `D100`–`D104`.
- mccabe `max-complexity = 10`.
- Charm source lives in `src/` and is imported flat (`from config_builder import
  ...`), never as `src.config_builder`. `pyproject.toml` sets
  `pythonpath = ["src", "lib"]` for pytest.
- No new entries in `charmcraft.yaml` `config.options`.
- With no `machine-observability` relation, the rendered config's labels must be
  unchanged from today's output: host metrics and Alloy self-metrics keep the
  `alloy-vm` unit's own topology.
- Commit messages use the repo's conventional prefixes (`feat:`, `fix:`,
  `docs:`, `refactor:`) and end with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Full verification before each commit: `uv run tox -e lint`,
  `uv run tox -e static`, `uv run tox -e unit`. The suite is 59 tests passing at
  the branch point.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/config_builder.py` | Split the local-metrics pipeline into host and self components, pin the interval, add the `HostMetricsCopy` input, and render one `prometheus.relabel` per copy. |
| `src/machine_observability_sources.py` | Build a ready-made `HostMetricsCopy` for the related workload from the topology it already parses. |
| `src/charm.py` | Collect the copies and pass them to `ConfigBuilder`; de-duplicate the two copies of the topology-label remap. |
| `README.md` | Document the per-unit copy behaviour under the Machine Observability section. |
| `docs/charm-architecture.md` | Add the host-metrics bullet to the topology-distinction list. |
| `tests/unit/test_config_builder.py` | Rendering tests for the split pipeline, the pinned interval, and the copies. Four existing assertions follow the realigned scrape block. |
| `tests/unit/test_charm.py` | One existing assertion follows the realigned scrape block. |
| `tests/unit/test_machine_observability_builder.py` | The translated source exposes a copy. |
| `tests/unit/test_machine_observability_relation.py` | Charm-level: two relations render two copies with their own topology. |

---

### Task 1: Split the local-metrics pipeline and pin its interval

**Files:**
- Modify: `src/config_builder.py:23` (constants), `:127-137` (`_render_base_blocks`), `:196-236` (the relabel and scrape renderers)
- Test: `tests/unit/test_config_builder.py`, `tests/unit/test_charm.py:1016`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `LOCAL_METRICS_SCRAPE_INTERVAL = "15s"` at module level in `config_builder`.
  - Two rendered component pairs — `discovery.relabel "host_metrics"` →
    `prometheus.scrape "host_metrics"` for the unix exporter, and
    `discovery.relabel "alloy_self"` → `prometheus.scrape "alloy_self"` for the
    `127.0.0.1:6987` target. Both scrapes keep `job_name = "alloy-local"` and the
    15s interval. Task 2 hangs the fan-out off `prometheus.scrape "host_metrics"`.
  - `_render_topology_rules(labels)`, which Task 2 reuses.

This task changes no metric labels. `discovery.relabel "local_metrics"` and
`prometheus.scrape "default"` cease to exist as component names; the series they
produced are unchanged because the target maps and label rules carry over verbatim.

The scrape block currently renders three `=`-aligned lines. Adding
`scrape_interval` makes it the longest key, so the block is realigned. That is
cosmetic, but it moves five existing assertions that match on the old spacing;
they are listed in Step 6 and are the only reason this task touches
`tests/unit/test_charm.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config_builder.py`:

```python
def test_host_metrics_and_alloy_self_render_as_separate_components():
    rendered = _builder(remote_write_endpoints=["http://mimir:9009/api/v1/push"]).build()

    assert 'discovery.relabel "host_metrics" {' in rendered
    assert "  targets = prometheus.exporter.unix.default.targets" in rendered
    assert 'discovery.relabel "alloy_self" {' in rendered
    assert '    __address__ = "127.0.0.1:6987",' in rendered
    assert 'prometheus.scrape "host_metrics" {' in rendered
    assert 'prometheus.scrape "alloy_self" {' in rendered
    assert 'discovery.relabel "local_metrics" {' not in rendered
    assert 'prometheus.scrape "default" {' not in rendered


def test_the_exporter_targets_do_not_reach_the_alloy_self_component():
    rendered = _builder().build()

    alloy_self = rendered.split('discovery.relabel "alloy_self" {', 1)[1].split("\n}", 1)[0]

    assert "prometheus.exporter.unix.default.targets" not in alloy_self


def test_both_local_scrapes_keep_the_alloy_local_job_and_a_pinned_interval():
    rendered = _builder().build()

    for component in ('prometheus.scrape "host_metrics" {', 'prometheus.scrape "alloy_self" {'):
        block = rendered.split(component, 1)[1].split("\n}", 1)[0]
        assert '  job_name        = "alloy-local"' in block
        assert '  scrape_interval = "15s"' in block


def test_both_local_components_carry_the_charm_topology_labels():
    rendered = _builder().build()

    for component in ('discovery.relabel "host_metrics" {', 'discovery.relabel "alloy_self" {'):
        block = rendered.split(component, 1)[1].split("\n}", 1)[0]
        assert '    target_label = "juju_unit"\n    replacement  = "alloy/0"' in block
```

Note on the slicing used throughout these tests: splitting on a newline-anchored
`"\n}"` finds a block's own closer and never its nested `  rule {` … `  }` pairs,
because those closers are indented. Do not "simplify" the slice to `"}"`, which
would stop at the first nested rule.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config_builder.py -v`
Expected: all four new tests FAIL — `discovery.relabel "host_metrics"` is not
rendered, and no `scrape_interval` is rendered anywhere today.

- [ ] **Step 3: Add the interval constant**

In `src/config_builder.py`, add the constant below `REMOTE_WRITE_MAX_KEEPALIVE`:

```python
REMOTE_WRITE_COMPONENT_NAME = "metrics"
REMOTE_WRITE_MAX_KEEPALIVE = "30m"

# Host metrics are cheap and their value is in the resolution, so the local
# scrape is pinned here rather than inheriting Alloy's one-minute default.
LOCAL_METRICS_SCRAPE_INTERVAL = "15s"
```

- [ ] **Step 4: Extract the shared rule renderer**

Add this method next to the other renderers:

```python
    def _render_topology_rules(self, labels: dict[str, str]) -> list[str]:
        """Render blank-line separated relabel rules that stamp topology labels."""
        rules: list[str] = []
        for key in self._topology_label_order():
            value = labels.get(key)
            if value:
                rules.extend(
                    [
                        "  rule {",
                        f'    target_label = "{key}"',
                        f"    replacement  = {json.dumps(value)}",
                        "  }",
                        "",
                    ]
                )
        if rules:
            rules.pop()
        return rules
```

- [ ] **Step 5: Replace the two renderers with four**

Delete `_render_local_metrics_relabel` and `_render_local_metrics_scrape`, and add:

```python
    def _render_host_metrics_relabel(self) -> str:
        return "\n".join(
            [
                'discovery.relabel "host_metrics" {',
                "  targets = prometheus.exporter.unix.default.targets",
                *(self._render_topology_rules(self._topology_labels) or [""]),
                "}",
            ]
        )

    def _render_alloy_self_relabel(self) -> str:
        """Render the relabel for Alloy's own metrics.

        The target map keeps its explicit ``job`` key, so Alloy's self-metrics
        carry the same job label they did when this target shared a component
        with the exporter's.
        """
        return "\n".join(
            [
                'discovery.relabel "alloy_self" {',
                "  targets = [{",
                '    job         = "alloy",',
                '    __address__ = "127.0.0.1:6987",',
                "  }]",
                *(self._render_topology_rules(self._topology_labels) or [""]),
                "}",
            ]
        )

    def _render_host_metrics_scrape(self) -> str:
        return "\n".join(
            [
                'prometheus.scrape "host_metrics" {',
                "  targets         = discovery.relabel.host_metrics.output",
                '  job_name        = "alloy-local"',
                f'  scrape_interval = "{LOCAL_METRICS_SCRAPE_INTERVAL}"',
                f"  forward_to      = {self._metrics_forward_to()}",
                "}",
            ]
        )

    def _render_alloy_self_scrape(self) -> str:
        return "\n".join(
            [
                'prometheus.scrape "alloy_self" {',
                "  targets         = discovery.relabel.alloy_self.output",
                '  job_name        = "alloy-local"',
                f'  scrape_interval = "{LOCAL_METRICS_SCRAPE_INTERVAL}"',
                f"  forward_to      = {self._metrics_forward_to()}",
                "}",
            ]
        )
```

Then emit them from `_render_base_blocks`:

```python
    def _render_base_blocks(self) -> list[str]:
        return [
            self._render_logging(),
            "",
            self._render_unix_exporter(),
            "",
            self._render_host_metrics_relabel(),
            "",
            self._render_alloy_self_relabel(),
            "",
            *([self._render_remote_write(), ""] if self._remote_write_endpoints else []),
            self._render_host_metrics_scrape(),
            "",
            self._render_alloy_self_scrape(),
        ]
```

- [ ] **Step 6: Update the five assertions that matched the old block**

Four match the old `=` alignment, one matches the old component names. Nothing
about their intent changes:

- `tests/unit/test_config_builder.py:43` — `'discovery.relabel "local_metrics" {'`
  becomes `'discovery.relabel "host_metrics" {'`
- `tests/unit/test_config_builder.py:44` — `'prometheus.scrape "default" {'`
  becomes `'prometheus.scrape "host_metrics" {'`
- `tests/unit/test_config_builder.py:45` — `'job_name   = "alloy-local"'` becomes
  `'job_name        = "alloy-local"'`
- `tests/unit/test_config_builder.py:46` — `"forward_to = []"` becomes
  `"forward_to      = []"`
- `tests/unit/test_config_builder.py:56` —
  `"forward_to = [prometheus.remote_write.metrics.receiver]"` becomes
  `"forward_to      = [prometheus.remote_write.metrics.receiver]"`
- `tests/unit/test_charm.py:1016` — `"forward_to = []"` becomes
  `"forward_to      = []"`

Leave every other `forward_to` assertion alone — `tests/unit/test_config_builder.py:152`
and `:307`, and `tests/unit/test_charm.py:288`, match Loki and syslog blocks,
which are not realigned.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_builder.py tests/unit/test_charm.py -v`
Expected: PASS. If any other assertion fails, do not rewrite it to fit — report it
with the exact assertion, its file:line, and the actual output.

- [ ] **Step 8: Verify and commit**

```bash
uv run tox -e lint && uv run tox -e static && uv run tox -e unit
git add -A
git commit -m "$(cat <<'EOF'
refactor: split host metrics from Alloy's own metrics

One relabel component fed both the exporter's targets and Alloy's own
endpoint, which is fine while they share a destination and wrong as soon
as host metrics need duplicating per workload -- the copies would carry
Alloy's self-metrics too.

Splitting them changes component names only. The target maps and label
rules carry over verbatim, so both series sets keep every label they had,
including the job label on Alloy's own metrics.

Both scrapes are pinned at 15s. Host metrics are cheap and their value is
in the resolution, so the interval no longer follows Alloy's one-minute
default. The block is realigned around the longer key, which moves five
assertions that matched the old spacing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Render one relabel component per copy

**Files:**
- Modify: `src/config_builder.py` — new `HostMetricsCopy` dataclass after `MetricsScrapeJob` (around `:36-48`), new `host_metrics_copies` parameter in `__init__` (around `:88-108`), fan-out in `_render_host_metrics_scrape`, new `_render_host_metrics_copy` and `_host_metrics_forward_to`, copies emitted from `_render_base_blocks`
- Test: `tests/unit/test_config_builder.py`

**Interfaces:**
- Consumes: `_render_topology_rules` and the split components from Task 1.
- Produces:
  - `HostMetricsCopy(component_name: str, topology_labels: dict[str, str])` —
    importable from `config_builder`.
  - `ConfigBuilder(..., host_metrics_copies: list[HostMetricsCopy] | None = None)`,
    keyword-only. Task 3 passes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config_builder.py`:

```python
OP_NODE_COPY = HostMetricsCopy(
    component_name="op-node_0",
    topology_labels={
        "juju_model": "base-mainnet",
        "juju_model_uuid": "00000000-0000-4000-8000-000000000002",
        "juju_application": "op-node",
        "juju_unit": "op-node/0",
        "juju_charm": "op-node",
    },
)
OP_RETH_COPY = HostMetricsCopy(
    component_name="op-reth_2",
    topology_labels={
        "juju_model": "base-mainnet",
        "juju_model_uuid": "00000000-0000-4000-8000-000000000002",
        "juju_application": "op-reth",
        "juju_unit": "op-reth/2",
        "juju_charm": "op-reth",
    },
)


def test_each_copy_renders_a_relabel_component_with_its_own_topology():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[OP_RETH_COPY, OP_NODE_COPY],
    ).build()

    op_node = rendered.split('prometheus.relabel "op_node_0" {', 1)[1].split("\n}", 1)[0]

    assert "  forward_to = [prometheus.remote_write.metrics.receiver]" in op_node
    assert '    target_label = "juju_unit"\n    replacement  = "op-node/0"' in op_node
    assert '    target_label = "juju_application"\n    replacement  = "op-node"' in op_node
    assert '    target_label = "juju_charm"\n    replacement  = "op-node"' in op_node
    assert 'prometheus.relabel "op_reth_2" {' in rendered


def test_the_host_scrape_forwards_to_remote_write_and_every_copy():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[OP_RETH_COPY, OP_NODE_COPY],
    ).build()

    assert (
        "  forward_to      = [prometheus.remote_write.metrics.receiver, "
        "prometheus.relabel.op_node_0.receiver, prometheus.relabel.op_reth_2.receiver]"
    ) in rendered


def test_the_alloy_self_scrape_never_forwards_to_a_copy():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[OP_NODE_COPY],
    ).build()

    alloy_self = rendered.split('prometheus.scrape "alloy_self" {', 1)[1].split("\n}", 1)[0]

    assert "  forward_to      = [prometheus.remote_write.metrics.receiver]" in alloy_self


def test_copies_are_dropped_without_a_remote_write_upstream():
    rendered = _builder(host_metrics_copies=[OP_NODE_COPY]).build()

    assert "prometheus.relabel" not in rendered
    assert "  forward_to      = []" in rendered


def test_no_copies_renders_only_the_collectors_own_pipeline():
    rendered = _builder(remote_write_endpoints=["http://mimir:9009/api/v1/push"]).build()

    assert "prometheus.relabel" not in rendered
    assert "  forward_to      = [prometheus.remote_write.metrics.receiver]" in rendered
```

Add `HostMetricsCopy` to the `from config_builder import ...` line at the top of
the file, keeping the imported names alphabetically ordered so ruff `I001` stays
clean.

The ordering assertion in `test_the_host_scrape_forwards_to_remote_write_and_every_copy`
is deliberate: the copies are passed `op-reth` first and must render `op_node_0`
first. Relation iteration order is not stable, and an unstable order would rewrite
the config text and restart Alloy for no reason.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config_builder.py -v`
Expected: the new tests FAIL with `ImportError: cannot import name 'HostMetricsCopy'`.
`test_no_copies_renders_only_the_collectors_own_pipeline` fails only on that
import; it passes once the import exists, which is the point — it pins that a
builder with no copies renders what Task 1 left behind.

- [ ] **Step 3: Add the dataclass and the parameter**

In `src/config_builder.py`, add the dataclass immediately after `MetricsScrapeJob`:

```python
@dataclass(frozen=True)
class HostMetricsCopy:
    """One extra copy of the host metrics, labelled for a workload on this machine.

    Host metrics describe the machine, and several Juju workloads can share one.
    Each copy re-stamps the single scrape's samples with one workload's own
    topology, so its metrics and the machine's arrive under the same labels.
    """

    component_name: str
    topology_labels: dict[str, str]
```

Add the parameter to `ConfigBuilder.__init__`, after `topology_labels`:

```python
        topology_labels: dict[str, str],
        host_metrics_copies: list[HostMetricsCopy] | None = None,
        log_source_groups: list[LogSourceGroup] | None = None,
```

And store it, sorted so the rendered order never depends on relation iteration:

```python
        self._topology_labels = topology_labels
        self._host_metrics_copies = sorted(
            host_metrics_copies or [], key=lambda copy: copy.component_name
        )
        self._log_source_groups = log_source_groups or []
```

- [ ] **Step 4: Render the fan-out**

Add the active-copies helper, the receiver list, and the copy renderer next to the
other renderers:

```python
    def _active_host_metrics_copies(self) -> list[HostMetricsCopy]:
        """Return the copies worth rendering, which needs somewhere to send them."""
        return self._host_metrics_copies if self._remote_write_endpoints else []

    def _host_metrics_forward_to(self) -> str:
        """Return the receivers for the one scrape of the exporter.

        Remote write takes the samples labelled for this unit; each copy's
        relabel component takes the same samples and re-stamps them.
        """
        receivers: list[str] = []
        if self._remote_write_endpoints:
            receivers.append(f"prometheus.remote_write.{REMOTE_WRITE_COMPONENT_NAME}.receiver")
        receivers.extend(
            f"prometheus.relabel.{self._sanitize_component_name(copy.component_name)}.receiver"
            for copy in self._active_host_metrics_copies()
        )
        return f"[{', '.join(receivers)}]"

    def _render_host_metrics_copy(self, copy: HostMetricsCopy) -> str:
        lines = [
            f'prometheus.relabel "{self._sanitize_component_name(copy.component_name)}" {{',
            f"  forward_to = {self._metrics_forward_to()}",
        ]
        rules = self._render_topology_rules(copy.topology_labels)
        if rules:
            lines.extend(["", *rules])
        lines.append("}")
        return "\n".join(lines)
```

Change one line in `_render_host_metrics_scrape` so it forwards to the copies:

```python
                f"  forward_to      = {self._host_metrics_forward_to()}",
```

`_render_alloy_self_scrape` keeps `self._metrics_forward_to()` — Alloy's own
metrics never reach a copy. `_metrics_forward_to()` also keeps serving the copies'
own `forward_to` and every relation-derived scrape job.

- [ ] **Step 5: Emit the copy blocks**

Change `_render_base_blocks` to append them after the two scrapes:

```python
    def _render_base_blocks(self) -> list[str]:
        blocks = [
            self._render_logging(),
            "",
            self._render_unix_exporter(),
            "",
            self._render_host_metrics_relabel(),
            "",
            self._render_alloy_self_relabel(),
            "",
            *([self._render_remote_write(), ""] if self._remote_write_endpoints else []),
            self._render_host_metrics_scrape(),
            "",
            self._render_alloy_self_scrape(),
        ]
        for copy in self._active_host_metrics_copies():
            blocks.extend(["", self._render_host_metrics_copy(copy)])
        return blocks
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_builder.py -v`
Expected: PASS.

- [ ] **Step 7: Verify and commit**

```bash
uv run tox -e lint && uv run tox -e static && uv run tox -e unit
git add -A
git commit -m "$(cat <<'EOF'
feat: render one host metric copy per workload unit

The exporter is scraped once and its samples fan out to a relabel
component per workload, each stamping that workload's own topology. The
collectors run once per interval no matter how many units share the
machine, and each workload sees the machine's metrics under the labels it
already queries its own metrics by.

Copies render only with a remote-write upstream to forward to, and render
in a sorted order so relation iteration cannot rewrite the config.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Wire the copies through the charm

**Files:**
- Modify: `src/machine_observability_sources.py:9` (imports), `:12-18` (the dataclass), `:77-80` (the return)
- Modify: `src/charm.py:26-34` (imports), `:405-416` and `:655-666` (the two topology-remap literals), the `ConfigBuilder(...)` call in `_render_config_text`, and the machine-observability helpers at the end of the class
- Modify: `README.md:48-63`, `docs/charm-architecture.md:69-76`
- Test: `tests/unit/test_machine_observability_builder.py`, `tests/unit/test_machine_observability_relation.py`

**Interfaces:**
- Consumes: `HostMetricsCopy` and `host_metrics_copies` from Task 2.
- Produces: nothing later tasks rely on. This is the last task.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_machine_observability_builder.py`:

```python
def test_translated_source_carries_a_host_metrics_copy():
    payload = MachineObservabilityPayload(
        schema_version=2,
        charm_name="op-node",
        source_topology=SourceTopology(
            model="base-mainnet",
            model_uuid="00000000-0000-4000-8000-000000000002",
            application="op-node",
            unit="op-node/0",
            charm_name="op-node",
        ),
    )

    source = translate_machine_observability_payload(payload)

    assert source.host_metrics_copy is not None
    assert source.host_metrics_copy.component_name == "op-node_0"
    assert source.host_metrics_copy.topology_labels == {
        "juju_model": "base-mainnet",
        "juju_model_uuid": "00000000-0000-4000-8000-000000000002",
        "juju_application": "op-node",
        "juju_unit": "op-node/0",
        "juju_charm": "op-node",
    }
```

That file currently imports only `from config_builder import ConfigBuilder,
FileLogSource, LogSourceGroup`. Add what the new test needs, keeping the block
ruff `I001`-clean — `charms.dwellir_observability...` sorts **before**
`config_builder`:

```python
from charms.dwellir_observability.v0.machine_observability import (
    MachineObservabilityPayload,
    SourceTopology,
)

from config_builder import ConfigBuilder, FileLogSource, LogSourceGroup
from machine_observability_sources import translate_machine_observability_payload
```

Append to `tests/unit/test_machine_observability_relation.py`:

```python
def _v2_payload(*, application: str, unit: str) -> str:
    return json.dumps(
        {
            "schema_version": 2,
            "charm_name": application,
            "source_topology": {
                "model": "base-mainnet-ovh-us-west-2",
                "model_uuid": "00000000-0000-4000-8000-000000000042",
                "application": application,
                "unit": unit,
                "charm_name": application,
            },
            "metrics_endpoints": [],
            "systemd_units": [],
            "journal_match_expressions": [],
            "log_files": [],
        }
    )


def test_every_related_workload_gets_a_host_metrics_copy():
    seen: dict[str, str] = {}
    with ExitStack() as stack:
        for manager in _patch_runtime():
            stack.enter_context(manager)
        stack.enter_context(
            patch(
                "charm.PrometheusRemoteWriteConsumer.endpoints",
                new_callable=PropertyMock,
                return_value=[{"url": "http://mimir:9009/api/v1/push"}],
            )
        )
        stack.enter_context(
            patch(
                "charm.alloy.write_config_text",
                side_effect=lambda config_text, **_: seen.__setitem__("config", config_text),
            )
        )
        harness = testing.Harness(AlloyCharm)
        harness.begin()
        harness.update_config({"systemd-units": "ssh.service"})

        for application, unit in (("op-reth", "op-reth/2"), ("op-node", "op-node/0")):
            relation_id = harness.add_relation("machine-observability", application)
            harness.add_relation_unit(relation_id, unit)
            harness.update_relation_data(
                relation_id,
                application,
                {"payload": _v2_payload(application=application, unit=unit)},
            )

    rendered = seen["config"]
    op_node = rendered.split('prometheus.relabel "op_node_0" {', 1)[1].split("\n}", 1)[0]

    assert '    target_label = "juju_unit"\n    replacement  = "op-node/0"' in op_node
    assert '    target_label = "juju_application"\n    replacement  = "op-node"' in op_node
    assert 'prometheus.relabel "op_reth_2" {' in rendered
    assert (
        "  forward_to      = [prometheus.remote_write.metrics.receiver, "
        "prometheus.relabel.op_node_0.receiver, prometheus.relabel.op_reth_2.receiver]"
    ) in rendered
    assert '    target_label = "juju_unit"\n    replacement  = "alloy-vm/0"' in rendered
    assert '      juju_unit = "alloy-vm/0",' in rendered


def test_no_related_workload_renders_no_copies():
    seen: dict[str, str] = {}
    with ExitStack() as stack:
        for manager in _patch_runtime():
            stack.enter_context(manager)
        stack.enter_context(
            patch(
                "charm.alloy.write_config_text",
                side_effect=lambda config_text, **_: seen.__setitem__("config", config_text),
            )
        )
        harness = testing.Harness(AlloyCharm)
        harness.begin()
        harness.update_config({"systemd-units": "ssh.service"})

    rendered = seen["config"]

    assert "prometheus.relabel" not in rendered
    assert '    target_label = "juju_unit"\n    replacement  = "alloy-vm/0"' in rendered
```

The last two assertions of the first test are the containment checks: the
`alloy-vm` unit keeps its own host-metric copy (`replacement  = "alloy-vm/0"` in
the `host_metrics` relabel), and the host journal log group
(`      juju_unit = "alloy-vm/0",` in `loki.process "juju"`) is untouched.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_machine_observability_builder.py tests/unit/test_machine_observability_relation.py -v`
Expected: `test_translated_source_carries_a_host_metrics_copy` FAILS with
`AttributeError: 'MachineObservabilitySource' object has no attribute
'host_metrics_copy'`; `test_every_related_workload_gets_a_host_metrics_copy`
FAILS because no `prometheus.relabel` block is rendered;
`test_no_related_workload_renders_no_copies` PASSES already, pinning the
no-relation behaviour.

- [ ] **Step 3: Build the copy in the translator**

In `src/machine_observability_sources.py`, extend the import:

```python
from config_builder import (
    FileLogSource,
    HostMetricsCopy,
    LogSourceGroup,
    MetricsScrapeJob,
    ScrapeTarget,
)
```

Add the field to the dataclass:

```python
@dataclass(frozen=True)
class MachineObservabilitySource:
    """Translated machine-observability inputs for one related principal."""

    metrics_scrape_jobs: list[MetricsScrapeJob]
    log_source_group: LogSourceGroup | None = None
    host_metrics_copy: HostMetricsCopy | None = None
```

And populate it in the return statement, reusing the label dict and component name
the translator already builds:

```python
    return MachineObservabilitySource(
        metrics_scrape_jobs=metrics_scrape_jobs,
        log_source_group=log_source_group,
        host_metrics_copy=HostMetricsCopy(
            component_name=_topology_component_name(topology.application, topology.unit),
            topology_labels=dict(labels),
        ),
    )
```

- [ ] **Step 4: Extract the topology-label helper**

`src/charm.py` spells out the same five-key remap literal twice — once in
`_render_config_text` and once in `_manual_metrics_scrape_jobs`. Add this method
directly above `_machine_observability_contract_error`:

```python
    def _topology_labels(self) -> dict[str, str]:
        """Return this unit's own Juju topology as Prometheus-style labels."""
        return self._topology.as_dict(
            remapped_keys={
                "model": "juju_model",
                "model_uuid": "juju_model_uuid",
                "application": "juju_application",
                "unit": "juju_unit",
                "charm_name": "juju_charm",
            }
        )
```

Replace both call sites with `self._topology_labels()`. The returned dict must be
identical to what those two sites produced before — this is a DRY extraction, not
a behaviour change.

- [ ] **Step 5: Collect and pass the copies**

Add `HostMetricsCopy` to the existing `from config_builder import (...)` list in
`src/charm.py`, keeping it alphabetically ordered.

Add this method directly below `_machine_observability_log_source_groups`:

```python
    def _machine_observability_host_metrics_copies(self) -> list[HostMetricsCopy]:
        """Return one host-metrics copy per workload sharing this machine."""
        return [
            source.host_metrics_copy
            for source in self._machine_observability_sources()
            if source.host_metrics_copy is not None
        ]
```

And pass it in `_render_config_text`, next to the existing log-source argument:

```python
            topology_labels=self._topology_labels(),
            host_metrics_copies=self._machine_observability_host_metrics_copies(),
            log_source_groups=self._machine_observability_log_source_groups(),
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_machine_observability_builder.py tests/unit/test_machine_observability_relation.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `uv run tox -e unit`
Expected: PASS. Pay attention to `tests/unit/test_charm.py` and
`tests/unit/test_manual_metrics_jobs.py` — they exercise the two call sites the
`_topology_labels()` extraction touches.

- [ ] **Step 8: Update the README**

In `README.md`, in the `### What gets rendered` list, replace this bullet:

```markdown
- its own local Alloy and host metrics labeled as `alloy-vm`
```

with:

```markdown
- its own local Alloy and host metrics labeled as `alloy-vm`, plus one copy of the
  host metrics per related workload under that workload's labels (see below)
```

Then add this subsection immediately after that list, before `### Example relations`:

```markdown
### Host metric attribution

`alloy-vm` collects host metrics for the whole machine rather than for itself, so
every workload related over `machine-observability` receives its own copy of them,
labelled with that workload's Juju topology:

- one copy per related unit, carrying that unit's `juju_model`,
  `juju_model_uuid`, `juju_application`, `juju_unit` and `juju_charm`
- one copy for the `alloy-vm` unit itself, carrying its own topology, exactly as
  before
- every copy keeps `job = "alloy-local"`, so existing queries still select host
  metrics; the copies differ only in their `juju_*` labels

A machine running `op-node/0` and `op-reth/2` therefore reports three sets of host
metrics: one per workload and one for the collector. That multiplies host-metric
series and remote-write volume by the number of related units plus one.

The exporter is scraped once regardless of how many units share the machine —
the samples are duplicated in Alloy's pipeline, not collected repeatedly. Copies
are rendered only when a remote-write upstream is related; without one there is
nowhere to send them.

Attribution is accurate when each related application has one unit per machine.
The payload is application-scoped, so a provider with units spread across machines
publishes a single unit name to all of them.

The job scrapes every 15s. Host metrics are cheap and their value is in the
resolution, so the interval is fixed rather than left at Alloy's one-minute
default.

Only host metrics are copied this way. Per-source metrics jobs and every log
stream, including host journal logs, keep the labels of the workload that declared
them, and Alloy's own metrics from `127.0.0.1:6987` stay on the `alloy-vm` unit's
topology alone.
```

- [ ] **Step 9: Update the architecture doc**

In `docs/charm-architecture.md`, add a third bullet to the "important topology
distinction" list, after the `machine-observability` log-inputs bullet:

```markdown
- host metrics are collected once and copied per related workload: each copy
  carries that provider's `source_topology`, and the `alloy-vm` unit keeps a copy
  under its own topology
```

- [ ] **Step 10: Verify and commit**

```bash
uv run tox -e lint && uv run tox -e static && uv run tox -e unit
git add -A
git commit -m "$(cat <<'EOF'
feat: give every related workload its own host metrics

A workload sharing the machine now receives the machine's metrics under
its own Juju topology, so a dashboard filtering on juju_application sees
the host it runs on without knowing anything about the collector.

The alloy-vm unit keeps its own copy, and host metrics stay on the
alloy-local job, so nothing that queries them today stops working.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Manual Validation

Not required to merge — the unit tests cover the rendering — but this is how to
confirm it on a real machine:

```bash
juju ssh alloy-vm/0 'grep -n "prometheus.relabel" -A 20 /etc/alloy/config.alloy'
juju ssh alloy-vm/0 'grep -n "prometheus.scrape \"host_metrics\"" -A 6 /etc/alloy/config.alloy'
```

Expect one `prometheus.relabel` block per related unit, each stamping that
workload's labels, and the `host_metrics` scrape forwarding to remote write plus
every one of those components.

Then confirm the copies arrive upstream as distinct series:

```bash
juju ssh mimir-vm/0 "curl -fsS 'http://127.0.0.1:9009/prometheus/api/v1/query?query=count+by+(juju_unit)+(node_load1)'"
```

Expect one entry per unit on the machine, including `alloy-vm/0`.

## Risks

- **Host-metric volume multiplies.** Every related unit adds a full copy of the
  host metrics — roughly a thousand series per copy at 15s. A machine with four
  workloads sends five times what it sends today. This is inherent to the
  approach, not an implementation detail, and it is the main reason to keep an
  eye on remote-write cost after rolling out.
- **Four times the sample rate on the collector's own copy.** Independently of
  the copies, pinning the scrape at 15s quadruples host-metric samples on every
  `alloy-vm` unit, including ones with no `machine-observability` relation.
  Series count is unchanged; this is ingest and storage rate.
- **`count`-style queries over host metrics change meaning.** Aggregations that
  did not previously group by a `juju_*` label now count each machine once per
  related unit. `count(node_load1)` becomes a count of units, not machines;
  `count(count by (instance) (node_load1))` still counts machines.
- **Component names in the rendered config change.** `local_metrics` and the
  `default` scrape become `host_metrics`, `alloy_self`, and their two scrapes.
  Nothing outside the config file references those names — no metric label
  changes — but anyone grepping a deployed `config.alloy` for the old names will
  not find them.
- **Every unit restarts Alloy once.** The rendered config text changes on
  upgrade even where no workload is related, and `_configure` restarts or reloads
  Alloy whenever the text differs from the last good config.
- **A payload with no `source_topology` contributes no copy.** The charm already
  blocks on `machine-observability` payloads that are present but not v2, so this
  only affects a relation whose databag is still empty; the copy appears on the
  next relation-changed hook.
- **`prometheus.relabel` rules with no `source_labels`.** The copies stamp labels
  with `target_label` + `replacement` and no `source_labels`, the same shape the
  existing `discovery.relabel` blocks already use. `alloy fmt` only parses and
  formats the config; it does not evaluate expressions or validate component
  argument schemas. If Alloy ever rejects that shape in `prometheus.relabel`
  specifically, the rendered config still passes `_configure`'s validation and
  gets written, and the rejection surfaces as a failed Alloy restart rather than
  a preserved previous config.
