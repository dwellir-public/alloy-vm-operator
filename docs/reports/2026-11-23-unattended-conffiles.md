# Unattended Alloy conffile repair

Scope: preserve modified conffiles in both APT install transactions. No relation,
configuration schema, ownership or blockchain workload changes.

| Requirement | Class | Before | Disposition | Target | Actual evidence |
| --- | --- | --- | --- | --- | --- |
| User: repair without SSH or juju exec | task-required; operational | Tor pending Alloy package configuration | Apply tested installer backport via charm refresh | Recover through hooks | Tor recovered to active/idle after local artifact refresh |
| Preserve charm-managed defaults | agent-proposed; installer | dpkg prompt fails without stdin | Set force-confold on both install commands | Keep defaults and finish pending configure | Unit regression passed |
| Unit coverage | required-baseline; shared | No conffile regression | Cover dependency install and Alloy install | Regression passes | 425 tests passed |
| Local artifact integration | required-baseline; shared | Not assessed | Deploy exact recovery artifact in disposable local model | Ready; config change works | PASS: exact backport installed Alloy 1.22.0; Jubilant listener round trip passed |

The recovery artifact backports only two APT command-line changes onto published
revision 40 (Ubuntu 24.04), avoiding unrelated ownership changes on main. Its
SHA256 is `ba53b4a886cd2055c7d28dda4dade1bfc4cf0c09ac81f6215b0c6a358ce35107`.
Only `src/alloy.py` differs from the downloaded artifact. No credentials are included.

Production target: `tor:joakimnyman@external/celo-mainnet-archive-rpc-lvm-xfs-ad`,
application `alloy-vm`. Local validation model:
`local:admin/celo-alloy-recovery-test-20261123` (disposable).

Unrelated charm standards were not audited. Durable apt-proxy repair remains
tracked separately in CLO-397. No stable promotion is included.

The real dpkg regression uses an isolated temporary package database/root,
unpacks a changed conffile, reproduces the EOF prompt failure, then configures
with force-confold and checks the original settings and empty package audit.
No host package database is changed.

Production recovery: `juju refresh --path` applied the exact tested artifact to
Tor alloy-vm; application is local revision 0, active/idle, Alloy 1.22.0, with
valid generated configuration. The deferred install completed through charm
hooks. The package database was not inspected directly after recovery because
SSH and juju exec are prohibited; success is evidenced by hook recovery and
live telemetry. No host shell access or arbitrary remote execution was used.
The disposable test model was destroyed. Frej was not changed.

The local repair remains deployed until the fix is released on Charmhub; no
claim of stable-channel publication is made. Neither op-node charm was refreshed
as part of this collector repair.

Post-repair validation over more than 60 seconds: Alloy readiness HTTP 200; fresh
Mimir up=1 and Loki entries for both op-node/0 and op-reth/1; execution RPC
reports eth_syncing=false and an advancing head. Exported process start times
for Alloy, op-node and Reth match the immediate pre-repair baseline. None of
these processes restarted during the repair.

Version distinction: the package and on-disk executable are 1.22.0, while live
alloy_build_info reports v1.21.3. Completing package configuration did not restart
the collector. No restart was added solely to align these versions.
