# North31 acceptance lab

Approved scope: Rocky 9.9, three full Gluster replicas, and a separate test
Engine. The broken `vscg` guest has no availability requirement. Finish by
deploying Rocky on north31 and rebuilding both approved data disks as XFS,
only after all preceding gates and verified off-host backups pass.

## Resources and isolation

`north31.py` creates only `nr99-*` guests on north31. It verifies the official
Rocky 9.9 installer SHA-256, refuses existing guest names, and installs only to
the guest's `vda`. It does not format host disks, delete existing guests, or
connect lab storage to the production VLAN.

- Engine: 4 vCPUs, 16 GiB RAM, 100 GiB OS disk.
- Three hosts: each 4 vCPUs, 16 GiB RAM, 80 GiB OS disk, two 40 GiB data disks.
- Management: `nr99-mgmt`, NAT `172.31.231.0/24`, DNS domain `nr99.test`.
- Storage: `nr99-storage`, isolated, VLAN 106 within guests, MTU 9000,
  `172.31.232.0/24`.
- Engine uses address `.10`; node1/node2/node3 use `.11`/`.12`/`.13`.
- Guest disks and consoles: `/var/lib/libvirt/images/nr99`.
- Helper staging and package evidence: `/var/lib/nr99`.

The operator public key is an execution input, not a repository secret. Use
SSH through north31 to reach the private lab management network. No production
Engine or Gluster pool is an enrollment target for this round.

The helper returns while installation continues. After the console confirms
installation completed and requested its first reboot, libvirt stops the
installer domain. Start the named guest with `virsh start nr99-node1` (or the
corresponding Engine/node name); its persistent definition boots the installed
disk. Do not treat installer creation as installation acceptance.

## Gates, in order

1. **RPM verification:** use `tools/verify-rocky9-rpms.sh host OUTPUT` and
   `engine OUTPUT` inside separate clean acceptance guests. Record complete
   manifests, repository policy, signing keys and fingerprints. Every selected
   RPM must contain a valid publisher signature. Reject missing signatures,
   unknown keys, unavailable sources, and automatic snapshot fallbacks. A
   successful DNF dependency solve alone does not satisfy this gate.
2. **Single-host provisioning:** install the accepted host package set with
   SELinux enforcing, correct administrator/Engine access, verified storage
   mounts, and restricted firewall services. Test an idempotent second run,
   reboot persistence, unavailable repositories, invalid disk identities,
   missing mounts, and recovery after an interrupted run. Never mark a failed
   run successful or reboot because of an installation failure.
3. **Integration:** build node2/node3 and the independent Engine, configure
   XFS-backed replica-3 storage, enroll hosts, attach a Gluster storage domain,
   boot a disposable nested guest, verify checksums, and live-migrate it.
4. **Recovery:** test one unavailable replica, a storage-network partition,
   and loss of two replicas. Expect one-node tolerance, write rejection on
   quorum loss, complete healing after restoration, unchanged checksums, and
   no split-brain. Timing measurements are observations, not production SLAs.
5. **MAAS end-to-end:** use `rocky9-9-packer-20261117` and a pinned provisioning
   commit. Prove publication of a fresh DHCP hostname into authoritative DNS
   before PXE acceptance. MAAS 3.6.6 currently lacks the expected DHCP lease
   notification socket; stop this gate if the functional test fails and address
   the controller separately. Test BIOS and UEFI VM deployment and two later
   boots. Scope preseed changes to test machines and run the established MAAS
   health checker around controller changes.
6. **Bare-metal:** export lab evidence and recoverable VM images, then capture
   and verify offline OS/data-disk recovery images on Janus before reimaging.
   Match the disk WWNs below immediately before any destructive action. Deploy
   the exact accepted Rocky image onto the OS disk; format one GPT/XFS partition
   per approved data disk, mount by UUID, then create brick subdirectories.
   Validate physical KVM, bonds, VLAN 106, MTU, management access, DNS/time,
   SELinux, bounded checksum I/O, and two reboots. Leave north31 on Rocky with
   glusterd available, without joining a production cluster.

| Role | Original device | WWN | Data mount |
| --- | --- | --- | --- |
| OS | `/dev/sda` | `0x644a84202a01c9002c6829760808a7a5` | — |
| Data 1 | `/dev/sdb` | `0x644a84202a01c9002c6929490787b062` | `/storage/sdb1` |
| Data 2 | `/dev/sdc` | `0x644a84202a01c9002c69294a079adbca` | `/storage/sdc1` |

## Evidence and rollback

Keep bulky logs, RPMs, disk images, and secrets outside Git. Commit package
manifests, accepted source/commit identifiers, and concise test outcomes only.
Retain recovery exports under a dedicated `north31-rocky99` directory on the
existing Janus backup share. Full-host reimaging removes the VM lab; virtual
acceptance and exports must be complete first. Restore the affected lab
checkpoint after a failed test. Restore the verified pre-test north31 image if
bare-metal deployment fails; other north hosts are not rollback targets.

Physical fault tolerance and performance across independent servers, production
Engine enrollment, data migration, and self-hosted Engine deployment are outside
this acceptance round. No gate may be marked passed on the strength of an
earlier dependency-only probe.

## Implemented lab helpers

Run the guest-side helpers as root only on their named private lab guest. The
Engine connection helper reads its password locally from a root-only directory;
API calls verify the Engine CA and never enable SDK debug logging. Engine API
login is `admin@ovirt@internalsso` for the packaged Keycloak configuration.

| Helper | Purpose |
| --- | --- |
| `setup_engine.py` | Initialize the independent Engine with accepted offline packages; keep generated answers/passwords root-only. |
| `offline_enrollment.py enter/restore` | Temporarily restrict host deployment to the accepted local RPM repository and restore the original repository directory afterward. |
| `enroll_hosts.py --hosts node1 node2 node3` | Enroll only the three private guests in the `nr99` data center and Broadwell cluster. |
| `firewall_policy.py` | Install and exercise oVirt's documented post-deployment site hook, scoped to the `nr99` cluster. |
| `gluster.py --known-hosts FILE` | From the operator's Mac, establish storage-only peer identities in both directions and create `nr99-data`/`nr99-io` as full replica-3 volumes. |
| `engine_storage.py` | Attach and activate only `nr99-data` as the test data domain. |
| `download_cloud_image.py` | Download the official Rocky 9.9 cloud image and verify its pinned SHA-256. |
| `nested_vm.py create/start/status/migrate` | Upload through verified TLS, create one disposable workload, and boot/migrate it between named lab hosts. |
| `guest_io.py prepare/verify/stop` | Inside the nested guest, retain a 16 MiB payload checksum and boot ID while running a bounded fsync journal. |
| `recovery.py --known-hosts FILE` | From the operator's Mac, inject virtual node power loss and isolated storage NIC partitions, restore faults, and verify all replicas and healing. |
| `dhcp_dns_probe.py` | On north31, use one temporary macvlan namespace to obtain a fresh MAAS lease and verify its hostname through authoritative DNS; release the lease and remove the namespace. |

Keep the verified operator `known_hosts` file outside Git. The operator public
key is staged separately. All VM and volume names, private addresses, and fault
targets are fixed to this disposable lab. These helpers do not enroll physical
hosts or select production storage.

Gluster brick allocation is configured to 55000–60999 before creating volumes,
avoiding oVirt's public migration and management ports. Provisioning refuses a
port-range change when volume metadata already exists. For a planned reboot of
an enrolled host, place it in Engine maintenance first and transfer its SPM
responsibilities; the power-loss fixture deliberately uses an abrupt virtual
power-off. A reboot without maintenance can wait indefinitely on storage leases.

Failed volume staging can leave a volume-id attribute and an empty `.glusterfs`
directory. `gluster.py --clear-incomplete-staging` is an explicit recovery option
that requires the volume to be absent on all peers and the brick to contain no
data. It removes only that empty metadata, never a registered volume or files.
