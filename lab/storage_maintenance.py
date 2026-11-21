#!/usr/bin/env python3
"""Quiesce or reactivate only the disposable nr99 workload and storage domain."""
import argparse
import json
from pathlib import Path
import time
from engine_api import connect


def wait(read, status):
    for attempt in range(90):
        value = read()
        if value.status.value == status:
            return
        time.sleep(2)
    raise RuntimeError('Timed out waiting for ' + status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['quiesce', 'activate'])
    args = parser.parse_args()
    with connect() as connection:
        system = connection.system_service()
        state = json.loads(Path('/var/lib/nr99/nested-vm.json').read_text())
        vm = system.vms_service().vm_service(state['vm_id'])
        if vm.get().name != 'nr99-workload':
            raise RuntimeError('Unexpected workload')
        dc = system.data_centers_service().list(search='name=nr99')[0]
        domains = system.data_centers_service().data_center_service(dc.id).storage_domains_service()
        domain = next(d for d in domains.list() if d.name == 'nr99-data')
        storage = domains.storage_domain_service(domain.id)
        if args.action == 'quiesce':
            if vm.get().status.value != 'down':
                vm.shutdown(force=False, reason='Isolated lab storage recovery maintenance')
                wait(vm.get, 'down')
            if storage.get().status.value != 'maintenance':
                storage.deactivate()
                wait(storage.get, 'maintenance')
        else:
            if storage.get().status.value != 'active':
                storage.activate()
                wait(storage.get, 'active')
            if vm.get().status.value == 'down':
                vm.start()
                wait(vm.get, 'up')
        print(json.dumps({'action': args.action, 'vm': vm.get().status.value,
                          'storage': storage.get().status.value}))


if __name__ == '__main__':
    main()
