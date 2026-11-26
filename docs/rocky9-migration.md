# Rocky 9.9 migration plan

Target: Rocky Linux 9.9 x86_64 hosts with **Gluster and oVirt**. Continue using
`East-Central-BOCES/northrack`; the operator has authorized committing and pushing
the updates to this repository. This plan and the package probe are the first
stage. The existing scripts are still the historical Rocky 8 implementation.

Implementation now follows the [approved north31 lab and acceptance gates](../lab/README.md).
The selected topology is three full replicas and an independent test Engine.
The final physical test includes rebuilding north31's two approved data disks
as XFS and leaving the host on Rocky 9.9. The broken `vscg` VM has no service
availability requirement. Reimaging remains gated on successful lab acceptance
and verified off-host recovery material.

Project completion also requires integration and testing of disconnect and
failed-heal alerts, verified recovery notification, and an operator recovery
procedure. On 2026-11-24 the operator deferred the monitoring-platform details
for later work. This remains an outstanding completion requirement.

## Round-2 closeout — 2026-11-26

The [results and next-work options](round2-results.md) and
[machine-readable evidence](../evidence/rocky99/round2-2026-11-26.json)
record the completed network-failure tests. All 809 earlier witnessed commits
and all 11 two-node-test witnessed commits survived manual replica repair and
clean VM restart. All 44 file/shard paths matched while quiescent, all 18 heal
clients were connected, and final heal-readiness checks passed. No loss or
alteration of witnessed durable commits was observed in those tests.

Automatic heal reconnect remains failed; monitoring integration, alert/recovery
tests and the reviewed operational runbook remain completion requirements.
Additional database/application crash testing is not a required deployment gate:
the platform/guest durability responsibility boundary is recorded in the closeout.
This does not certify physical power-loss durability or the online-but-stale
replica case, and grants no storage-gate waiver or production promotion.
MAAS/DNS readiness work and storage operational handoff are the next options;
physical reimaging remains behind acceptance and verified off-host backups.

## Execution record — 2026-11-19

- North31's missing VM utilities were installed using a separate apt metadata
  cache because an unrelated `apt-get update` had held the default cache lock
  for ten days. That process and the host's apt source files were left intact.
- The official Rocky 9.9 minimal ISO passed SHA-256 verification:
  `56772c6c07464ea8e55b64cb5ec2448d16e1b4673b8041678dacfd540131401c`.
- All four independent lab guests installed and booted Rocky 9.9 with SELinux
  enforcing. The installer-media package list excludes Git, which is absent
  from the minimal ISO. Invalid Kickstart `--bootproto=none` was replaced with
  explicit offline NetworkManager keyfile configuration. Failed attempts are retained.
- The storage parent and VLAN persist MTU 9000 across reboot. The implementation
  activates a uniquely identified inactive parent, enables autoconnect, disables
  parent DHCP, and reconciles both saved profiles and live interfaces.
- Gate 1 passed: all **1,013 host RPMs** and **1,020 Engine RPMs** have valid
  signatures. Ceph Pacific packages are signed with the published CentOS
  Storage SIG key; enabling repository GPG checking works without a signature
  bypass. Only keys referenced by enabled repositories are imported.
- Accepted manifests include RPM and publisher-key file hashes. Host manifest:
  `69bcb2f87f39aacd2006125ed1745c25acff2c523cbba17143b1b6b312949d60`;
  Engine manifest:
  `c8d1e55c44b87723af2c2fece3e560d2c894b19cce3026337fb1a0e4660beddf`.
  Full manifests are committed under `evidence/rocky99`; bulk RPMs and logs are
  retained outside Git in the MAAS workspace and acceptance VMs.
- The first Engine install identified a conditional `sssd-nfs-idmap` dependency
  omitted by the download dependency walk. Verification now also performs a
  native DNF download-only installation transaction against the actual guest's
  installed packages. The corrected signed set installed successfully.
- Gate 2 passed: all three hosts provisioned from the accepted local cache;
  node1 retained its configuration after reboot and a second run changed zero
  tasks. Six live rejection fixtures passed (bad manifest, missing cache, OS
  disk, wrong mount, management interface, foreign mounted filesystem).
  Node2 was terminated after formatting and before mounting, then resumed with
  unchanged filesystem UUIDs. Failed runs did not write success markers.
- The independent Engine initialized successfully. TLS validation and authenticated
  API access work; credentials remain root-only on the Engine. All three hosts
  enrolled using source-restricted public-key SSH and accepted local RPMs only.
- Vendor host deployment reopened Gluster on the public zone. The documented
  Engine post-deployment site hook now restores the lab's storage-only rule.
  Brick allocation starts at 55000 to avoid oVirt's publicly allowed migration
  ports 49152–49216 and management/image service ports 54321–54323.
  Post-enrollment host health checks pass on all three nodes.
- Powered-off checkpoints preserve the pre-provisioning, pre-Engine-setup and
  pre-enrollment states. No physical disk has been rebuilt. Integration and
  recovery tests are in progress; MAAS and bare-metal acceptance remain pending.
- Ansible reports that EPEL's `ansible.posix` collection does not advertise
  compatibility with the supplied ansible-core 2.14.18. The modules used here
  execute successfully in native tests; this warning remains recorded and is
  not suppressed. The branch is not promoted into the production deployment path.

## Recovery investigation — 2026-11-20

The interrupted recovery observation eventually timed out after 15 minutes.
All injected links were restored. On resumption, all Engine hosts and the nested
VM were up, with three pending data-volume entries on two replicas and no
reported split-brain. The nested payload checksum and original boot ID still
matched; its bounded writer completed all 1,800 journal entries.

Two self-heal daemons retained disconnected clients. Explicit daemon recovery
on node1/node3 preserved all brick processes and restored successful heal
commands. A repeat baseline write then failed the direct-replica checksum
guard: the existing FUSE client did not create the file on node3. No new fault
was injected, and quorum testing remained gated.

The disposable VM was gracefully shut down, its data domain put in maintenance,
and the standalone I/O mount released. Full heals succeeded with the workload
quiescent. All 40 file/shard paths then matched across all three replicas;
both volumes reported zero pending entries and the data volume zero split-brain.
The domain and VM were reactivated with fresh clients. The VM started on node3
and then successfully live-migrated back to node1. Its original payload checksum
still matched after this planned maintenance boot. Another partition test
ran from this recovered state.
This manual recovery does not establish automatic partition recovery acceptance.

`recovery.py` now requires successful heal triggers as well as connected bricks,
zero pending heals and zero split-brain. Its final error distinguishes readiness
failure from an actual split-brain report. `check_replicas.py` compares quiescent
file/shard contents; `repair_shd.py` is an explicit lab repair, never an automatic
step hidden inside the acceptance test.

The fresh-client repeat passed baseline checksums on all three replicas and
accepted a 16 MiB fsynced write with node3's storage link down. After link
restoration it failed the full 900-second healing-readiness window again.
Quorum-loss testing was not attempted. Diagnostic dumps on all three nodes
showed both heal clients for node3 disconnected, while the other four clients
remained connected. The portmapper correctly published node3's new data/I/O
ports 57368/55205; both listeners were reachable over storage, jumbo pings
passed, and management-side access remained blocked. Both volumes reported
zero split-brain entries. This establishes a reproducible client-reconnection
problem; it does not establish the underlying code defect or a production fix.

The configured stable CentOS SIG EL9 Gluster 11 repository still offers only
11.0 and the installed 11.1-1.el9s. Upstream has an
[11.2 release](https://github.com/gluster/glusterfs/releases/tag/v11.2), but it is
not an accepted EL9 package candidate and was not installed or assumed to fix
this failure. Both quorum policies, SELinux, and the storage firewall remain in
place. Explicit quiescent recovery after this failed test preserved every brick
process, restored all six heal-client connections on each node, and passed
heal readiness. All 42 file/shard paths matched across the three replicas with
zero pending heals and zero split-brain. All three host health checks passed.
The verified storage domain is active again, all three Engine hosts are up,
and the disposable workload is running on node1 with its original payload
checksum intact. The final native provisioning rerun completed with 25 tasks
OK, zero changes and zero failures; all 19 unit tests pass. Automatic partition
recovery remains failed and deployment remains gated. The concise machine-readable
record is [recovery-2026-11-20.json](../evidence/rocky99/recovery-2026-11-20.json).

Full diagnostic logs are retained outside Git under
`artifacts/northrack/implementation` in the MAAS workspace. Read-only protocol
diagnostics are implemented in `probe_portmap.py`; `shd_state.py` extracts only
active connection flags from self-heal statedumps. Publisher protocol sources:
[portmap XDR](https://github.com/gluster/glusterfs/blob/v11.1/rpc/xdr/src/portmap-xdr.x)
and [RPC identifiers](https://github.com/gluster/glusterfs/blob/v11.1/rpc/rpc-lib/src/protocol-common.h).

Online research during the investigation covered the upstream troubleshooting
guide, issue reports, package availability, and the 11.1/11.2 client-handshake
and RPC sources. Related reports were rechecked on 2026-11-24:

- [Gluster #2492](https://github.com/gluster/glusterfs/issues/2492) describes
  self-heal/client connectivity and port mismatches on older 8.x/9.x releases.
  It references a release-9 fix; it does not establish a fix for our 11.1 case.
- [Gluster #4274](https://github.com/gluster/glusterfs/issues/4274) describes
  11.1 port/reconnection trouble and stalled healing after brick restarts.
  The author's recovery reports do not establish that our reproduction has
  the same cause or a released software fix.
- [Gluster #4028](https://github.com/gluster/glusterfs/issues/4028) concerns
  Gluster 11 client/brick disconnections during heavy writes or rebalance.
  Its initiating workload differs from our storage-link partition.

These are research leads, not a confirmed upstream diagnosis. No reviewed
report or candidate package has yet been demonstrated to fix our exact test.

## Baseline and deployment boundary

The reviewed legacy revision is `9f20bea6c120d69f1df190cc30749705320cf4d9`.
The production MAAS preseed `curtin_userdata_centos` contains commented commands
to clone this repository into `/prov/northrack` and run `prepare.sh`.

The saved Rocky 9 custom preseed already provisions named SSH administrators,
disables root SSH login and password authentication, installs Ansible and Gluster
11, and restricts Gluster to storage VLAN 106. Verify its live contents before
integration; keep one owner for each setting rather than adding competing logic
to both MAAS and this repository.

The changes here must support fresh deployment. Updating an existing oVirt engine,
joining its cluster, and migrating existing Gluster data are separate operations
requiring the actual engine version, cluster compatibility level, storage layout,
and maintenance sequence. The old scripts do not establish those details.

## Required replacements

| Legacy behavior | Rocky 9 implementation requirement |
| --- | --- |
| Rocky PowerTools file edits | Enable CRB through the package manager. |
| Gluster 8 and oVirt 4.4 release RPM | Select and validate EL9 repositories and exact candidate versions. |
| Multiple overlapping release-package dependencies | Explicitly select the Gluster major version and exclude testing repositories. |
| `@reboot` cron, sleeps, file moves, unconditional reboots | Use a logged, rerunnable provisioning entry point with explicit prerequisites and error handling. If automatic first boot is needed, use a systemd oneshot ordered after cloud-init and network readiness. |
| `runonce.sh` archives scripts regardless of exit status | Record success only after all required tasks and acceptance checks succeed; retain failures for retry. |
| Hard-coded root and engine keys | Preserve the current named-user policy and parameterize any engine access required by the selected oVirt enrollment method. |
| Gluster opened in the default firewall zone | Preserve the VLAN 106 storage-zone boundary across NetworkManager reloads and reboots. |
| Hard-coded `/storage/sdb1` and `/storage/sdc1` directories | Identify mounted filesystems by UUID and validate mounts before creating brick directories. Never silently put bricks on the OS filesystem. |
| Blanket passwordless `ovirtgluster` root-group account | Determine whether the current oVirt workflow needs the account; grant only the access that workflow requires. |

## Repository investigation

Checked on 2026-11-19 against a disposable x86_64 Rocky 9.9 container under
emulation on Apple ARM. Metadata is available from the enabled EL9 repositories;
that does not by itself prove package signatures, a solvable combined transaction,
installation, or a working hypervisor.

The Rocky Extras `centos-release-ovirt45-9.2-1.el9` package pulls in Gluster 10
and additional SIG release packages. Installing `centos-release-gluster11-1.0-1.el9`
alongside it leaves both Gluster versions enabled. The Gluster 11 release package
also enables `centos-gluster11-test`, whose configuration has `gpgcheck=0`.
Disable Gluster 10 and the Gluster 11 testing repository before evaluating the
intended transaction. Audit every remaining repository and the actual package
sources before approving an installation; for example, the pulled-in Ceph Pacific
repository also has signature checking disabled in its supplied configuration.
The combined dry run actually selects Ceph Pacific packages, including
`ceph-common` and `librados2`; this is not merely an unused enabled repository.
Establish and verify the signature policy for these dependencies before installation.

Initial candidates after disabling the two Gluster repositories above:

| Package | Candidate | Source repository |
| --- | --- | --- |
| `ovirt-host` | `4.5.1-1.el9` | `centos-ovirt45` |
| `ovirt-hosted-engine-setup` | `2.7.2-1.el9` | `centos-ovirt45` |
| `vdsm-gluster` | `4.50.6.1-1.el9` | `centos-ovirt45` |
| `cockpit-ovirt-dashboard` | `0.16.3-1.el9` | `centos-ovirt45` |
| `gluster-ansible-roles` | `1.0.5-28.el9` | `centos-ovirt45` |
| `glusterfs-server`, `glusterfs-fuse` | `11.1-1.el9s` | `centos-gluster11` |
| `glusterfs-selinux` | `2.0.1-1.el9s` | `centos-gluster11` |

The current oVirt RHEL-derivative instructions recommend a master snapshot COPR,
while the alternate download page also documents `centos-release-ovirt45`.
Start by evaluating the SIG package candidates above; do not silently opt into
snapshots to solve dependency failures. Repository URLs alone are not a version
lock: record the full resolved transaction and image digest, then establish a
repeatable package source or retained RPM set for deployment.

## Implementation and acceptance sequence

1. Reproduce combined dependency resolution natively on x86_64 with the package
   probe below. Resolve missing dependencies, unverified sources, and conflicting
   providers before writing the new installer. Download and verify the selected
   RPMs before marking the repository set accepted.
2. Add a Rocky 9-only provisioning entry point and idempotent Ansible tasks for
   the chosen repository set, packages, services, access, and firewall policy.
   Remove the reboot chain from the new deployment path. Keep explicit diagnostics
   for unsupported OS releases and unavailable repositories.
3. Validate on a disposable native x86_64 Rocky 9.9 VM, including installation,
   service startup, SELinux enforcing, KVM availability, management connectivity,
   storage networking, DNS/time synchronization, and reboot recovery. Re-running
   successful provisioning must make no unintended changes. Simulated repository
   failures must stop provisioning without a success marker or automatic reboot.
4. Confirm the engine topology (existing engine enrollment or new self-hosted
   engine), required engine SSH access, exact storage mounts and Gluster topology.
   Test engine enrollment and a disposable VM using a test Gluster storage domain.
   Package installation alone is not acceptance of Gluster/oVirt integration.
5. Connect MAAS to a reviewed commit of the new entry point and test an end-to-end
   fresh deployment. Preserve evidence and the previous preseed and repository
   revision for rollback. Test changes on a branch in this repository, then promote
   the tested revision into the deployment path.

## Reproducing the package probe

The native x86_64 probe passed on `10.10.10.111` on 2026-11-19 with a 1 GiB
memory cap. It resolved a combined transaction of **800 workload packages**,
**435 MiB** download size as reported by DNF, and **1.6 GiB** installed size.
The requested oVirt, VDSM, hosted-engine, Cockpit, Ansible, and Gluster packages
resolved together without adding a snapshot repository. This was an
`--assumeno` transaction: workload installation and runtime acceptance remain
pending. The test container was automatically removed after the probe.

Test image: `rockylinux:9`, reported OS Rocky Linux 9.9, architecture `amd64`,
digest `sha256:03303863808e9cd79f0c58c833db3e5f9cde9f2abe16804a267b05dc54af86e4`.
The full native probe log is retained in the MAAS upgrade workspace at
`artifacts/northrack/native-rocky99-package-probe-1g.log`.

`tools/check-rocky9-packages.sh` refuses to run outside a container or on an OS
other than Rocky 9.9 x86_64. It installs repository-release packages, disables the
two conflicting/testing Gluster sources, refreshes metadata without ignoring
unavailable sources, and uses DNF's solver to plan the combined transaction. It
does not install workload packages, start services, or modify disks.

From this checkout on an x86_64 Docker host, use a disposable container:

```sh
docker run --rm --memory=1g --memory-swap=1g --cpus=1 --pids-limit=128 \
  -v "$PWD:/work:ro" rockylinux:9 bash /work/tools/check-rocky9-packages.sh
```

The rolling image tag must still report Rocky 9.9 or the probe stops. Record its
resolved digest with each result. The transaction report lists the proposed
packages, versions, architectures, and source repositories; the configuration
dump includes the signature policy for each repository. Successful resolution
is not approval to install from those sources.

The operator selected `10.10.10.111` (`tech-3950`) for native x86_64 testing. Its
read-only inventory on 2026-11-19 found Ubuntu 22.04.5, working `/dev/kvm`, a
running 16 GiB `vscg` VM, existing Docker workloads, and about 1.6 GiB available
RAM with swap nearly full. Keep the initial probe resource-capped. Full VM
acceptance needs sufficient free capacity; do not stop or resize existing
workloads as part of this package probe. A first probe capped at 512 MiB was
killed while loading repository metadata; use the 1 GiB limit above and recheck
host capacity before running it.

## Primary references

- [oVirt 4.5.7 release notes](https://www.ovirt.org/release/4.5.7/)
- [oVirt RHEL and derivative installation](https://www.ovirt.org/download/install_on_rhel.html)
- [oVirt alternate installation methods](https://www.ovirt.org/download/alternate_downloads.html)
- [Rocky repository documentation](https://docs.rockylinux.org/teams/rel_eng/repo/)
- [Rocky 9 Extras release packages](https://dl.rockylinux.org/pub/rocky/9/extras/x86_64/os/Packages/c/)
- [CentOS SIG EL9 oVirt packages](https://mirror.stream.centos.org/SIGs/9-stream/virt/x86_64/ovirt-45/)
- [CentOS SIG EL9 Gluster 11 packages](https://mirror.stream.centos.org/SIGs/9-stream/storage/x86_64/gluster-11/)
