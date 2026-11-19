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

## Execution record — 2026-11-19

- North31's missing VM utilities were installed using a separate apt metadata
  cache because an unrelated `apt-get update` had held the default cache lock
  for ten days. That process and the host's apt source files were left intact.
- The official Rocky 9.9 minimal ISO passed SHA-256 verification:
  `56772c6c07464ea8e55b64cb5ec2448d16e1b4673b8041678dacfd540131401c`.
- `nr99-node1` and `nr99-engine` installed and booted Rocky 9.9 with SELinux
  enforcing. The installer-media package list was corrected to exclude Git,
  which is not available on the minimal ISO. First-attempt logs are retained.
- The guest's VLAN initially remained at MTU 1500. Explicitly configuring the
  parent and VLAN to 9000 and disabling parent DHCP corrected it; future lab
  Kickstarts explicitly configure the parent.
- Gate 1 passed: all **1,013 host RPMs** and **1,019 Engine RPMs** have valid
  signatures. Ceph Pacific packages are signed with the published CentOS
  Storage SIG key; enabling repository GPG checking works without a signature
  bypass. Only keys referenced by enabled repositories are imported.
- Accepted manifests include RPM and publisher-key file hashes. Host manifest:
  `69bcb2f87f39aacd2006125ed1745c25acff2c523cbba17143b1b6b312949d60`;
  Engine manifest:
  `14f40ea152d306edb08314fcb67d265d245ddd6db13da582159c717bd3df2a4f`.
  Full manifests are committed under `evidence/rocky99`; bulk RPMs and logs are
  retained outside Git in the MAAS workspace and acceptance VMs.
- Powered-off `before-provisioning` checkpoints were taken for both acceptance
  VMs, including node1's blank data disks. No physical disk has been rebuilt.
- Provisioning, integration, recovery, MAAS deployment, and bare-metal acceptance
  remain in progress or pending; the historical scripts are not promoted.

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
