# Round 2: operator-paced stale-replica safety test

Prepared 2026-11-24; updated 2026-11-25. **Native validation in progress.**
Local unit tests and syntax checks have passed. The installed systemd version
(255) and Gluster status XML layout were checked through read-only commands.
Native bootstrap and stage-by-stage review remain required.

## Continuous in-VM write test (2026-11-25)

Production read-only inspection found all 19 attached disks across 12 running
VMs on north33/34/36 using `cache=none`, `error_policy=stop`, `io=threads`.
These are observed effective settings, not a claim that every value was
inherited from an oVirt default. No production setting was changed.

The no-write control subsequently FAILED: node3's I/O-brick heal clients on
nodes2/3 remained disconnected; the data-volume clients recovered. All 44
paths still matched. No outage write is necessary to trigger this reconnect
failure, and per-volume SHDs do not prevent it.

The requested `continuous-prepare` restores the known failed I/O client pattern
with the existing targeted helper, verifies all 18 connections and 44 paths,
resumes the workload, and checks its effective disk policies against production.
It passed in 90.4 seconds; the lab VM uses the same three settings.

`continuous-write` executes the following single-node network-fault experiment:

1. Recheck VM/domain/hosts, zero backlog/split-brain, all 18 connections, guest
   identity and clock alignment. Require ten valid independently witnessed
   commits before fault injection. A new UUID trial keeps earlier evidence.
2. Inside the VM, append deterministic numbered approximately 4 KiB records for
   150 seconds. Flush and fsync each record; check read-after-fsync; only then
   emit its sequence and checksum over a dedicated management SSH connection.
   Parent directories are fsynced when the new transaction file is created.
3. North31 validates each ACK against independently generated expected content,
   appends it to a private witness journal on north31's own filesystem, and
   fsyncs that journal. The witness is outside Gluster and survives guest loss.
4. Disconnect node3 storage for 35 seconds under the independent restoration
   timer. Continue writing through node1 throughout; restore automatically.
   No second-node fault or daemon repair occurs in this job.
5. Read back the entire transaction file, check order/content and every
   witnessed ACK. Valid extra unwitnessed records or an unwitnessed partial
   tail are not automatically called data loss. Missing or altered witnessed
   records, unexpected complete record content, or wrong read-after-fsync
   results are failures. Timeouts/uncertain progress are INCONCLUSIVE.
6. Verify the original 16 MiB payload against the previously saved SHA and the
   original 1,800 journal entries. Report heal connections separately; a
   write-integrity PASS does not imply automatic healing recovered.

The dedicated root-only north31 key's public half is authorized for the named
`rocky` user inside this disposable VM, restricted to source `172.31.231.1`.
The private half stays on north31; no Mac key is copied. Guest scripts live at
`/home/rocky/nr99-round2-code`; original payload/oracle files are never modified.
Remove this lab key entry when retiring the test harness.

The service limit is 15 minutes. North31 retains trial config, fault timestamps,
external witness, writer stderr/outcome and guest checksum inventory under
`continuous-TRIAL_UUID`. Results explicitly report the number of commits wholly
inside the outage (with two-second timestamp margins) and whether those records
were preserved. Clock uncertainty refuses fault injection.

This first phase verifies live I/O and readback following a network interruption.
`post_reboot_verified=false` remains explicit: witness verification after reboot,
application/database recovery, second-node loss, and abrupt power-loss durability
are still separate unfinished tests. A network-test PASS does not certify them.

First live-write run evidence (trial `726b30d80d8b4df49a6c4a0f70cfbd73`): all
809 witnessed commits preserved; no partial tail; original payload and 1,800
journal entries intact; all 18 SHD clients connected at the final observation.
Sequence 11's fsync blocked for 36.119 seconds, spanning the 35-second outage,
and completed approximately 1.123 seconds after the recorded restoration window.
The writer exited normally with no witness errors. Zero commits completed wholly
within the outage. This is acceptable fail-stop/stall evidence, not a silent
write-loss observation. The original FAILED receipt is retained: it reflected
the harness's outage-progress requirement. Its intended INCONCLUSIVE exception
was misclassified because script/module imports created distinct exception
classes; the harness now shares one exception type. No old receipt is rewritten.

The next requested stage is `continuous-reboot` (20-minute service limit):
validate the independent witness against deterministic expected records, read
and verify the current transaction file, capture the guest boot ID, gracefully
stop the workload/deactivate its domain, reactivate/start it, require a changed
boot ID, and re-read every witnessed transaction plus the original payload and
journal. Retain pre/post checksum inventories in the existing trial directory,
and verify heal status and all 18 connections afterward. It writes no new test
transactions, injects no network fault, and restarts no storage daemon. It leaves
the workload running on success. No second-node-loss stage starts automatically.
This is a graceful reboot test, not abrupt power-loss durability certification.

Reboot verification subsequently PASS in 89.3 seconds. Guest boot ID changed;
all 809 witnessed commits, original payload and 1,800 retained journal entries
survived; all 18 SHD connections and final heal-info checks were healthy.

### Sequential second-node-loss test

The next separately requested job is `two-node-write` (20-minute service limit).
It requires the successful reboot receipt and a fresh healthy live baseline.
A new trial/witness keeps the earlier 809-record evidence unchanged. The writer
also reports BEGIN attempts separately from fsync ACKs; attempts are not commits.

After ten witnessed commits, isolate node3 storage for 45 seconds. If the writer
is still alive, verify node3's link is still down, then isolate node2 storage
for 35 seconds. Restore node2 then node3 before awaiting writer completion.
Nested fault contexts, the worker's final cleanup, and independently armed
360-second north31 timers protect both links. No physical link or production
host is changed. A fired watchdog invalidates timing.

New fsync operations completed wholly inside the one-reachable-replica window
(excluding two-second timing margins) must not be acknowledged. Such ACKs fail
the quorum-safety check even if later readback matches. A write started before
that window may already have replicated, so it is not automatically called an
unsafe ACK. Every witnessed commit still must match independently reconstructed
content after restoration. Blocked operations, explicit safe I/O errors, or a
storage-induced VM pause are acceptable availability failures, recorded as such.
There must be actual evidence of blocking/errors/pause, not just an empty window.

If Engine reports the lab VM paused after both links return, wait at most two
minutes for heal-info readiness without restarting storage daemons; then use
Engine's start action only for the existing paused workload on its active domain
to resume it. Refuse other states rather than force stopping/restarting the VM.
This follows the [official oVirt module's paused-VM handling](https://github.com/oVirt/ovirt-ansible-collection/blob/master/plugins/modules/ovirt_vm.py).
Libvirt's interactive management authentication is not bypassed or changed.

The resulting receipt distinguishes transaction preservation from heal-client
connection health. No daemon repair runs after the fault; degraded connections
remain evidence for review. Any unreadable inventory or timeout is inconclusive,
not proof that data survived. The original payload and journal are also checked.

This tests two overlapping node storage outages with the VM on unaffected node1.
It does not establish the separate current-plus-online-stale replica scenario,
which previous natural healing prevented us from reaching. Nor does it certify
abrupt power-loss durability or the physical redundant network.

## Earlier round-2 evidence

Bootstrap and preflight subsequently passed. Case
`376f5ef9443b4ae88b995c143e6504d6` created and verified all nine cold snapshots
and restarted the three hosts, then failed during Engine activation with a
host communication error. SSH readiness preceded VDSM readiness. The corrected
runner waits for host management services and the Engine-to-host listener, and
retries only this specific network error within a bounded activation deadline.

The separately requested `checkpoint-finish` stage validates the retained
checkpoint receipt, saved snapshot verification records, current VM UUIDs and
disk paths; completes activation; checks heal readiness; and leaves workload
and data domain quiescent. It does not repeat snapshots or overwrite the original
failure. A PASS here satisfies the checkpoint prerequisite for `baseline`.

The first completion attempt activated node2, but stopped on its transient
`unassigned` state before it became UP. The runner now tolerates that state
only after its own accepted activation request. Explicit `--attempt 2` on
`start`, `show` and `wait` uses separate service, log and result names, retaining
the original failure. Stage prerequisites use the latest completed attempt.

The baseline passed. The first scenario stopped INCONCLUSIVE because node3's
raw canary already matched G1 after its link returned; node2 was not disconnected.
Both links were confirmed UP, with no watchdog receipt. The separately requested
`automatic-verify` stage observes heal-info/backlog/split-brain, compares the
acknowledged canaries and both quiescent replica trees, and captures SHD client
connection flags. It never issues a heal trigger or restarts daemons. SHD
connection inspection requests a diagnostic statedump (SIGUSR1). Its PASS
would verify recovery for this run, not answer the untested second-failure case
or erase the earlier recovery failure. Use the usual `start`/`wait`/`show`
commands with `--step automatic-verify`.

Automatic verification found 44 matching file/shard paths and no reported heal
backlog/split-brain, but disconnected SHD clients. Diagnostic capture found
node1 at 6/6 connections and nodes2/3 each at 4/6, missing both node3 brick
clients. Node3 brick ports changed; node1 discovered new ports while node2's
log selected old ports. Evidence is saved under the case's
`connections-20261125T001846Z` directory and in operator-side artifacts.

The explicitly requested `targeted-recover` stage requires this exact connection
pattern and zero backlog/split-brain before proceeding. It restarts only nodes2/3
SHDs using the existing brick-preserving helper, waits up to three minutes for
all 18 connections, checks node1's SHD PID stayed unchanged, then verifies heal
status and all acknowledged writes and replica trees. Its service limit is
25 minutes including checksums. It does not restart the workload or overwrite
the failed automatic-recovery result.

Targeted recovery PASS (37.3 seconds): only nodes2/3 SHDs restarted, all brick
processes preserved, node1 SHD PID unchanged, all 18 client connections restored,
all 44 file/shard paths matched, acknowledged canary preserved, and heal
backlog/split-brain checks passed. This verifies the scoped manual workaround
for this run. Automatic reconnect remains a failed acceptance condition; the
second-current-node-loss safety case remains INCONCLUSIVE/unexecuted.

`resume` may follow the combined verification in a successful `targeted-recover`
receipt, or the original separate `verify` stage. It rechecks quiescence,
heal-info and all connections before activating the domain and starting the VM.
After its operator-reported completion, perform one original-payload checksum
check via the existing Mac SSH identity. That identity is not copied to north31
and the dedicated round-2 key is not authorized inside the nested workload.
Compare `/home/rocky/nr99-acceptance/payload` with the original SHA-256 in
`state.json`; also check the retained journal's continuity. The old journal
writer is finished and the VM has intentionally rebooted, so neither an active
writer nor the original boot ID is expected. Do not regenerate the payload or
overwrite the original oracle.

Resume subsequently PASS: workload UP, data domain ACTIVE, all hosts UP. The
operator-side read verified the original 16 MiB payload SHA-256
`5d654a1dc48fda04b99586e20c6307d7cb7ef3f802525e062cd75e458a1522f1`
unchanged and all 1,800 journal entries contiguous.

The next requested diagnostic is `control-no-write`: quiesce the workload,
verify healthy connections and all replica content, release canary mounts,
disconnect node3 storage for 35 seconds with the same independent watchdog,
restore it, allow a fixed 60-second observation window, and capture all daemon
PIDs/connections before verifying heal-info and content again. It issues no
writes during the outage, no heal triggers, and no daemon restarts. Both links
are restored on exit; workload remains quiescent for result review. Its limit
is 30 minutes including checksums and graceful quiescence.

This is a test of recurrence after manual recovery, not a clean isolation of
write effects: nodes2/3 now run separate per-volume SHDs whereas their original
shared SHDs were replaced. Before/after PIDs and connection maps are retained.
Existing cold checkpoints and all previous receipts remain intact; no snapshot
rollback or reimaging is performed. A failure preserves evidence before repair.

## Objective

Test the assumption that losing another current replica while node3 remains
stale produces safe I/O failure or safe healing, without serving older content
or losing acknowledged writes. This is a separately labelled safety experiment
in the disposable VM lab. It does not mark the earlier failed automatic-recovery
gate passed or advance MAAS/physical deployment.

Keep Rocky 9.9, Gluster 11.1-1.el9s, full replica-3, client quorum auto, server
quorum server/51%, VLAN 106, MTU 9000, and the existing firewall/SELinux policies.
Use only `nr99-io` canary files. Quiesce the nested workload and its data domain;
verify VM disk shards during final recovery. Do not format or modify physical
disks, change repositories, or enroll production hosts.

## How we will work together

1. Start **one** named stage in a detached systemd service on north31.
2. Return to chat immediately with the case ID, service name and result path.
3. Let the script perform its bounded waits locally. No ChatGPT polling loop.
4. The operator reports that the stage completed, or reports a failure.
5. Read its saved result once, review it, then explicitly start the next stage.

No stage starts the next stage automatically. Completion is not the same as a
PASS. Failed and inconclusive receipts are retained, never overwritten.
Recovery stages remain available after a failed/inconclusive experiment.

The two network-fault phases belong to **one scenario job**. Do not pause for
human confirmation with a cable disconnected or between establishing the stale
replica and the second failure; that would change the experiment. Each fault
has an independently armed north31 systemd timer that restores that node's
storage NIC after at most six minutes. The worker also restores links in
`finally`. A fired watchdog invalidates the test timing and makes the result
inconclusive. Management NICs stay connected.

## Step 0: bootstrap and review, on request

This is preparation only; it must not inject faults. Before running any stage:

- Confirm the existing `nr99-engine`, `nr99-node1`/`2`/`3` inventory and fixed
  storage MACs `52:54:00:99:02:0b`, `:0c`, `:0d` on network `nr99-storage`.
- Stage root-owned code in `/var/lib/nr99/round2/code` on north31 and the four
  private guests. Preserve local SHA-256 staging receipts. Do not update code
  while a case is running.
- Stage these repository files: `lab/staged_safety.py`, `round2_probe.py`,
  `round2_engine.py`, `engine_api.py`, `storage_maintenance.py`, `repair_shd.py`,
  `shd_state.py`, and `tools/check_host.py` (basename `check_host.py`).
- Create a dedicated root-only lab SSH key on north31 at
  `/var/lib/nr99/round2/id_ed25519`. Keep its private half on north31 only; never
  copy the Mac's private key. Add its public half to named user `jarredm` on the
  four lab guests, retaining existing entries. Restrict the entry to source
  `172.31.231.1` with OpenSSH `restrict`; first verify that this is north31's
  management-bridge source address. No production account changes.
- Copy the already verified lab host-key file to
  `/var/lib/nr99/round2/known_hosts`. Require strict host-key checking.
  Do not generate trust with an unchecked key scan or disable host-key checks.
- Set the root directory, code and key files root-owned with private permissions
  (directory 0700; private key 0600). Verify batch SSH and `sudo -n` from north31
  to `.10`–`.13`, and root readability of existing host configuration files
  `/home/jarredm/nr99-nodeN-engine.json`.
- Verify Engine-side imports of the installed SDK and the staged connection
  helper without printing credentials. Passwords stay in the existing root-only
  Engine secret directory; the helper retains CA verification/debug disabled.
- Inspect `--help`, package versions, healthy-state XML parsing, timer command
  support and the proposed VM/disk targets before launching checkpoint work.

Bootstrap has not been implemented as an unattended installer. We will perform
and review this one-time setup with the existing operator SSH access. Its lab
key must be removed when this test runner is retired.

## Stages and stopping points

| Stage | Script operation | Result to review before continuing |
| --- | --- | --- |
| `checkpoint` | Gracefully stop the nested VM, deactivate its storage domain, put hosts in Engine maintenance, release canary mounts, cleanly shut down the three replica VMs, save domain/network XML and verified internal qcow2 snapshots of all nine OS/data images, restart hosts, return them to UP. | Nine verified snapshots and UUID/path receipt; hosts UP; workload DOWN and data domain in maintenance. No force power-off. |
| `baseline` | Check host health, exact Gluster packages, both quorum settings and heal readiness. Mount an existing client, write/fsync generation G0, verify its checksum directly on all three replicas. | Three current, matching copies. Any baseline problem stops before faults. |
| `scenario` | Execute the bounded sequence below, then restore both links before returning. | Safety findings, rejected vs timed-out operations, external write oracle and automatic cleanup outcome. |
| `recover` | Verify workload/domain quiescence, restore links, refuse split-brain/unknown states, unmount canary clients, explicitly restart heal daemons while preserving bricks, launch full heals and wait up to 15 minutes. | Successful heal triggers; connected bricks; zero backlog/split-brain. This is manual recovery, not a pass for automatic recovery. |
| `verify` | Compare every acknowledged canary write on all replicas, compare both quiescent volume trees including VM disk shards, verify all six heal-client connections on each host. | All checksums and connections match. No PASS from timeouts or missing inventories. |
| `resume` | Reactivate the data domain and start the nested workload on node1. | Hosts/domain/VM active; afterward make one operator-side SSH check of the original guest payload checksum using the existing Mac key. |

Checkpoints are lab-local rollback material, not the verified off-host backups
required for physical reimaging. The Engine remains running; its topology is not
changed. A checkpoint failure can leave replica VMs off or hosts in maintenance:
stop and inspect the receipt/log. Do not force power-off, take live `qemu-img`
snapshots, or blindly restart after a snapshot error.

Rollback, if needed, must be reviewed separately: quiesce the workload/domain,
place hosts in maintenance and cleanly stop **all three replicas**; validate
the nine image paths, VM UUIDs and common snapshot label in `checkpoint.json`;
apply the same checkpoint to all nine images with `qemu-img snapshot -a` only
while offline; then restart and verify. Never restore a single replica's older
data into the active pool as an automatic response.

## Scenario sequence

1. Revalidate G0 on all three replicas and require workload/domain quiescence.
2. Arm and verify node3's independent restoration timer **before** disconnecting
   its storage NIC. Wait 35 seconds; write/fsync G1 through node1. Record the ACK
   and checksum in an oracle on north31, outside Gluster. Verify G1 on nodes1/2.
3. Restore node3's link and wait up to 60 seconds for all I/O-volume bricks to
   return online. Do not issue heal commands in this window. Read node3's file
   directly: it must still demonstrably contain G0. If it has healed or its
   state is ambiguous, stop INCONCLUSIVE and inject no second fault. Natural
   recovery is useful evidence; do not disable healing or edit AFR metadata to
   manufacture a stale copy.
4. Arm node2's independent timer, disconnect its storage NIC and wait 35 seconds.
   Node1 is current; node3 is online but was stale at the start of this phase.
5. Through the existing client, read the G1 record and attempt a separate canary
   write. Repeat using a newly mounted client. A new client may legitimately
   heal node3 before allowing I/O, so capture raw replica observations as well.
6. Restore node2's link; ensure both links are up, record results, and stop.

All writes go through Gluster. Direct brick access is read-only checksum
verification. No operation chooses a split-brain winner or writes Gluster's
internal metadata directly.

Accept either current data or an explicit safe I/O error. A stale read is
critical. An acknowledged probe write must have two matching available copies.
Timeouts, failed fresh mounts, unexpected errors and missing prerequisites are
INCONCLUSIVE, not proof of safe rejection. Failed writes may leave partial or
completed files: their existence does not mean the application received an ACK.
Final recovery must preserve every recorded acknowledged write; it must not
assume unacknowledged files are absent.

This tests the named canary workload only. It does not certify all application
crash semantics or physical-server fault tolerance. The short/long outage and
with/without-write diagnostic matrix can follow as separately reviewed cases;
it is not automatically launched by this runner.

## Commands after bootstrap

Run on north31 as root. The `new` command records the three expected VM UUIDs
and returns a case ID; substitute that ID below.

```sh
sudo /usr/bin/python3 /var/lib/nr99/round2/code/staged_safety.py new
sudo /usr/bin/python3 /var/lib/nr99/round2/code/staged_safety.py start --case CASE_ID --step checkpoint
```

`start` returns immediately. Results and logs are private files under
`/var/lib/nr99/round2/cases/CASE_ID/`. The authoritative completion file is
`checkpoint.result.json` (and similarly for each later stage).

The operator can optionally wait in a terminal without involving ChatGPT:

```sh
sudo /usr/bin/python3 /var/lib/nr99/round2/code/staged_safety.py wait --case CASE_ID --step checkpoint
```

Closing that terminal does not stop the detached worker or its watchdogs.
After the operator reports completion, make one result read:

```sh
sudo /usr/bin/python3 /var/lib/nr99/round2/code/staged_safety.py show --case CASE_ID --step checkpoint
```

Review, then start exactly one of `baseline`, `scenario`, `recover`, `verify`,
or `resume` using the same `start` command. A missing receipt is not completion:
inspect the named service and log once if the service failed before writing a
receipt. Do not repeatedly poll from chat. An emergency link-only restoration
is available with `restore-links --case CASE_ID`; it does not repair data or
claim test success.

Keep the current monitoring integration requirement deferred but outstanding.
These local completion files are not the production monitoring implementation.
