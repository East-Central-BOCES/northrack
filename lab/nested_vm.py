#!/usr/bin/env python3
"""Upload, boot, and migrate the single disposable nr99 workload VM."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import ovirtsdk4.types as types
from ovirt_imageio import client
from engine_api import connect
from download_cloud_image import NAME, SHA256, checksum

STATE = Path('/var/lib/nr99/nested-vm.json')


def wait_for(read, predicate, label, seconds=300):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        item = read()
        if predicate(item):
            return item
        time.sleep(2)
    raise RuntimeError('Timed out: ' + label)


def save(state):
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n')
    temporary.replace(STATE)


def create(system, key_path):
    if STATE.exists():
        raise RuntimeError('Workload state already exists; inspect it before repeating create')
    if system.vms_service().list(search='name=nr99-workload'):
        raise RuntimeError('An existing workload VM has no matching local receipt')
    image = Path('/var/lib/nr99') / NAME
    if checksum(image) != SHA256:
        raise RuntimeError('Cloud image checksum mismatch')
    key = key_path.read_text().strip()
    if len(key.splitlines()) != 1 or not key.startswith(('ssh-rsa ', 'ssh-ed25519 ')):
        raise RuntimeError('An operator public SSH key is required')
    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(image)]))
    disks = system.disks_service()
    disk = disks.add(types.Disk(name='nr99-workload-root', format=types.DiskFormat.COW,
                               provisioned_size=info['virtual-size'], sparse=True,
                               storage_domains=[types.StorageDomain(name='nr99-data')]))
    state = {'disk_id': disk.id, 'image_sha256': SHA256, 'phase': 'disk-created'}
    save(state)
    disk_service = disks.disk_service(disk.id)
    wait_for(disk_service.get, lambda item: item.status == types.DiskStatus.OK, 'disk creation')
    transfers = system.image_transfers_service()
    transfer = transfers.add(types.ImageTransfer(
        disk=types.Disk(id=disk.id), format=types.DiskFormat.RAW,
        direction=types.ImageTransferDirection.UPLOAD,
        inactivity_timeout=180, timeout_policy=types.ImageTransferTimeoutPolicy.CANCEL))
    transfer_service = transfers.image_transfer_service(transfer.id)
    try:
        transfer = wait_for(transfer_service.get,
                            lambda item: item.phase == types.ImageTransferPhase.TRANSFERRING,
                            'transfer readiness', 90)
        print(json.dumps({'upload': 'started', 'disk': disk.id}), flush=True)
        client.upload(str(image), transfer.transfer_url, '/etc/pki/ovirt-engine/ca.pem',
                      secure=True, proxy_url=transfer.proxy_url, max_workers=2)
        transfer_service.finalize()
        transfer = wait_for(transfer_service.get,
                            lambda item: item.phase in (types.ImageTransferPhase.FINISHED_SUCCESS,
                                                       types.ImageTransferPhase.FINISHED_FAILURE),
                            'upload finalization')
        if transfer.phase != types.ImageTransferPhase.FINISHED_SUCCESS:
            raise RuntimeError('Image upload verification failed')
    except Exception:
        transfer_service.cancel()
        state['phase'] = 'upload-failed'
        save(state)
        raise
    state['phase'] = 'image-uploaded'
    save(state)
    dc = system.data_centers_service().list(search='name=nr99')[0]
    network_ids = {network.id for network in system.networks_service().list()
                   if network.name == 'ovirtmgmt' and network.data_center.id == dc.id}
    profiles = [profile for profile in system.vnic_profiles_service().list()
                if profile.name == 'ovirtmgmt' and profile.network.id in network_ids]
    if len(profiles) != 1:
        raise RuntimeError('Expected exactly one nr99 management vNIC profile')
    vm = system.vms_service().add(types.Vm(
        name='nr99-workload', description='Disposable Rocky 9.9 storage and migration acceptance VM',
        cluster=types.Cluster(name='nr99'), template=types.Template(name='Blank'),
        memory=2 * 1024 ** 3, memory_policy=types.MemoryPolicy(guaranteed=1024 ** 3),
        cpu=types.Cpu(topology=types.CpuTopology(sockets=1, cores=2, threads=1)),
        bios=types.Bios(type=types.BiosType.Q35_SEA_BIOS),
        os=types.OperatingSystem(type='rhel_9x64', boot=types.Boot(devices=[types.BootDevice.HD])),
        initialization=types.Initialization(host_name='nr99-workload', user_name='rocky',
                                            authorized_ssh_keys=key),
        placement_policy=types.VmPlacementPolicy(affinity=types.VmAffinity.MIGRATABLE)))
    state['vm_id'] = vm.id
    state['phase'] = 'vm-created'
    save(state)
    service = system.vms_service().vm_service(vm.id)
    wait_for(service.get, lambda item: item.status == types.VmStatus.DOWN, 'VM creation')
    service.disk_attachments_service().add(types.DiskAttachment(
        disk=types.Disk(id=disk.id), interface=types.DiskInterface.VIRTIO_SCSI,
        bootable=True, active=True))
    service.nics_service().add(types.Nic(name='nic1', interface=types.NicInterface.VIRTIO,
                                        vnic_profile=types.VnicProfile(id=profiles[0].id)))
    state['phase'] = 'ready'
    save(state)
    print(json.dumps({'workload': state, 'passed': True}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'start', 'status', 'migrate'])
    parser.add_argument('--public-key', type=Path)
    parser.add_argument('--host', choices=['node1', 'node2', 'node3'], default='node1')
    args = parser.parse_args()
    with connect() as connection:
        system = connection.system_service()
        if args.action == 'create':
            if not args.public_key:
                parser.error('--public-key is required for create')
            create(system, args.public_key)
            return
        state = json.loads(STATE.read_text())
        service = system.vms_service().vm_service(state['vm_id'])
        if service.get().name != 'nr99-workload':
            raise RuntimeError('Workload identity mismatch')
        host = system.hosts_service().list(search='name=nr99-' + args.host)[0]
        if args.action == 'start':
            service.start(use_cloud_init=True, vm=types.Vm(
                placement_policy=types.VmPlacementPolicy(
                    affinity=types.VmAffinity.MIGRATABLE, hosts=[types.Host(id=host.id)])))
            wait_for(service.get, lambda vm: vm.status == types.VmStatus.UP, 'VM start')
        elif args.action == 'migrate':
            service.migrate(host=types.Host(id=host.id))
            wait_for(service.get, lambda vm: vm.status == types.VmStatus.UP and vm.host.id == host.id,
                     'live migration', 300)
        vm = service.get()
        current_host = system.hosts_service().host_service(vm.host.id).get().name if vm.host else None
        print(json.dumps({'vm_id': vm.id, 'status': vm.status.value, 'host': current_host,
                          'nics': [{'mac': nic.mac.address} for nic in service.nics_service().list()]}), flush=True)


if __name__ == '__main__':
    main()
