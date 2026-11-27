"""Translate v2 machine-observability payloads into Alloy VM config inputs."""

from __future__ import annotations

from dataclasses import dataclass

from charms.dwellir_observability.v0.machine_observability import MachineObservabilityPayload

from config_builder import FileLogSource, LogSourceGroup, MetricsScrapeJob, ScrapeTarget


@dataclass(frozen=True)
class MachineObservabilitySource:
    """Translated machine-observability inputs for one related principal."""

    metrics_scrape_jobs: list[MetricsScrapeJob]
    log_source_group: LogSourceGroup | None = None


INTERNAL_IP_LABEL = "internal_ip"


def translate_machine_observability_payload(
    payload: MachineObservabilityPayload,
    internal_ip: str = "",
) -> MachineObservabilitySource:
    """Translate one v2 payload into builder-ready metrics and log inputs.

    internal_ip is this machine's address. It labels metrics targets only, and
    an endpoint label of that name takes precedence over it.
    """
    if payload.source_topology is None:
        msg = "machine-observability payload requires source_topology"
        raise ValueError(msg)

    topology = payload.source_topology
    charm_name = topology.charm_name or payload.charm_name
    labels = {
        "juju_model": topology.model,
        "juju_model_uuid": topology.model_uuid,
        "juju_application": topology.application,
        "juju_unit": topology.unit,
    }
    if charm_name:
        labels["juju_charm"] = charm_name

    # A payload is application data, so only the collector can say which machine a target is on.
    collector_labels = {INTERNAL_IP_LABEL: internal_ip} if internal_ip else {}

    metrics_scrape_jobs: list[MetricsScrapeJob] = []
    job_base_name = _topology_component_name(topology.application, topology.unit)
    for index, endpoint in enumerate(payload.metrics_endpoints):
        job_name = job_base_name if index == 0 else f"{job_base_name}_{index}"
        metrics_scrape_jobs.append(
            MetricsScrapeJob(
                job_name=job_name,
                targets=[
                    ScrapeTarget(
                        address=target,
                        labels={**collector_labels, **endpoint.labels, **labels},
                    )
                    for target in endpoint.targets
                ],
                metrics_path=endpoint.path,
                scheme=endpoint.scheme,
                scrape_interval=endpoint.interval,
                scrape_timeout=endpoint.timeout,
                tls_config=dict(endpoint.tls),
            )
        )

    file_log_sources = [
        FileLogSource(
            include=list(source.include),
            exclude=list(source.exclude),
            attributes=dict(source.attributes),
        )
        for source in payload.log_files
    ]

    log_source_group = None
    if payload.systemd_units or payload.journal_match_expressions or file_log_sources:
        log_source_group = LogSourceGroup(
            component_name=_topology_component_name(topology.application, topology.unit),
            topology_labels=labels,
            systemd_units=list(payload.systemd_units),
            journal_match_expressions=list(payload.journal_match_expressions),
            file_log_sources=file_log_sources,
        )

    return MachineObservabilitySource(
        metrics_scrape_jobs=metrics_scrape_jobs,
        log_source_group=log_source_group,
    )


def _topology_component_name(application: str, unit: str) -> str:
    """Build a stable component name from workload topology."""
    if "/" not in unit:
        return application
    unit_suffix = unit.rsplit("/", 1)[1]
    return f"{application}_{unit_suffix}"
