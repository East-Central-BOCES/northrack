# Rocky 9.9 storage test results

Recorded 2026-11-26 (America/Denver). Scope: disposable `nr99` VM lab on
north31; no production network fault or storage-policy change was made.
The [machine-readable record](../evidence/rocky99/round2-2026-11-26.json)
was checked against an exported archive of the original receipts and logs.
Detailed execution history remains in the [round-2 plan](../lab/ROUND2-PLAN.md).

## Verified outcomes

| Exercise | Observed result |
| --- | --- |
| Storage interruption without outage writes | Heal-client reconnect failure recurred. Application writes are not required to trigger it. |
| Continuous in-VM fsync writes; one node isolated for 35 seconds | One fsync blocked for 36.119 seconds, then writing resumed. All 809 externally witnessed commits were unchanged, including after a clean VM restart. |
| Sequential node3 then node2 storage isolation; 35-second overlap | No witnessed new commits during quorum loss. Engine temporarily reported the VM not responding. All 11 witnessed commits were present after recovery; one additional complete unwitnessed record was permissible. |
| Automatic recovery after the two-node test | Incomplete: 11/18 heal clients connected and VM-data heal backlog remained, despite workload availability. No split-brain was reported by the checks performed. |
| Manual recovery, replica comparison, and VM restart | PASS: all 44 file/shard paths matched while quiescent, all 18 connections and heal-readiness checks passed, the guest boot ID changed, and both the 11-commit and earlier 809-commit witnesses matched afterward. |

The original 16 MiB payload remained unchanged and all 1,800 retained journal
entries were contiguous. At the final verification the workload and all three
hosts were UP and the data domain ACTIVE. These describe the recorded result,
not a continuously monitored statement of current health.

VM disk settings matched the inspected production policies: `cache=none`,
`error_policy=stop`, `io=threads`. Gluster was 11.1-1.el9s, full replica-3,
client quorum auto, server quorum server/51%, SELinux enforcing, and storage
traffic restricted to its lab network.

The first live-write receipt remains FAILED because its harness required
commits to complete during the outage; the actual observation was safe blocking
with preserved witnessed data. The two-node receipt remains INCONCLUSIVE because
the runner stopped on a transient Engine state before readback. Subsequent
comparison and recovery receipts provide the additional evidence; none of the
original results was rewritten. Startup-readiness errors in the harness were
also retained and corrected before continuing.

## Conclusion and responsibility boundary

There was **no observed loss or alteration of externally witnessed fsync
commits in the failures exercised**. A successful ordinary buffered write is
not equivalent to a durable application commit. The platform must honor guest
durability requests and expose storage failure; the guest OS/application must
issue those requests correctly and recover interrupted operations. A separate
database/application crash test is not a required gate for this deployment
project, as agreed with the operator on 2026-11-26.

Automatic heal reconnection remains defective. Manual recovery was verified,
including preservation of brick processes during SHD restarts. This is not an
automatic-recovery acceptance pass, a confirmed upstream code diagnosis, or a
production rollout approval. Monitoring/runbook work does not itself fix the
defect. Before deployment advances past this gate, document a demonstrated fix
or an explicit operator decision on accepting the manual recovery procedure
and its operational prerequisites. No such exception is granted by this record.

Still unproven: the online-but-stale replica plus another current-node loss
case, abrupt physical power loss and hardware write-cache durability, and
failure behavior across independent physical hosts and the actual redundant
network. Do not describe this finite lab test as a universal no-corruption
guarantee. The VM lab cannot validate physical network HA.

## Monitoring: open project-completion requirement

**Outstanding; not implemented or tested.** The operator's existing monitoring
platform, recipients, timing thresholds and escalation policy remain to be
specified. Carry this requirement through deployment and handoff:

- Detect node/brick disconnection, disconnected active SHD clients, failed heal
  operations, and nonzero healing backlog that stops making progress.
- Derive the expected connection set from actual volume/replica topology; the
  lab's 18 connections are not a production-wide constant. Inspect current
  active graphs rather than obsolete statedump graphs.
- Treat VM availability, live brick processes and zero reported backlog as
  insufficient recovery evidence on their own. We observed disconnected heal
  clients with matching data and zero reported backlog.
- Keep the incident open until expected connections, brick availability and
  healing are verified, with no split-brain and no pending backlog. Define an
  observation period and explicit recovery-notification policy in that system.
- Test alert delivery and clearance using a controlled lab failure, including
  the case where VM service returns before background recovery completes.
- Deliver a reviewed operator runbook: preserve evidence, quiesce affected
  workloads/storage when needed, stop for unknown/split-brain states, perform
  the scoped SHD repair, wait for daemon readiness, heal and verify replicas,
  restore workloads and verify committed data. Do not choose a split-brain
  winner automatically. Existing helpers are fixed to the disposable lab;
  production targeting/access still require a reviewed implementation.

## Next work options

1. **MAAS deployment readiness (recommended next).** Recheck the existing
   DHCP-to-authoritative-DNS blocker and current controller health, then prepare
   a pinned Rocky image/provisioning commit and test-machine-only preseed path
   for BIOS/UEFI VM deployment. This can progress without new destructive tests.
   Formal end-to-end acceptance and any physical reimage remain behind the
   unresolved storage gate; controller changes require their own scoped review.
2. **Storage operational handoff.** Turn the lab-tested recovery sequence into
   a reviewed runbook and a monitor-independent health-reporting contract, with
   fixtures for disconnected clients and stalled healing. The actual monitoring
   integration stays open until the operator's platform is specified. Include
   the decision on fixing automatic reconnect versus accepting manual recovery;
   do not silently waive that gate or automate repair just to clear alerts.

Physical deployment comes later, after the relevant acceptance gates and verified
off-host recovery exports. The local evidence archive is not a substitute for
the required Janus VM/disk recovery backups. No new test or controller change
was launched as part of this closeout.
