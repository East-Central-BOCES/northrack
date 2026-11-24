#!/usr/bin/env python3
"""Engine-side preparation and status for the operator-paced nr99 tests."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['quiesce', 'status', 'maintain-hosts', 'activate-hosts', 'resume'])
    args = parser.parse_args()
    if os.geteuid() or socket.gethostname() != 'engine.nr99.test':
        raise SystemExit('Restricted to root on the independent nr99 Engine')
    from engine_api import connect
    if args.action in ('quiesce', 'resume'):
        subprocess.run(['python3', str(Path(__file__).with_name('storage_maintenance.py')),
                        'quiesce' if args.action == 'quiesce' else 'activate'],
                       check=True, capture_output=True, text=True, timeout=450)
    with connect() as connection:
        system = connection.system_service()
        dc = system.data_centers_service().list(search='name=nr99')[0]
        domains = system.data_centers_service().data_center_service(dc.id).storage_domains_service()
        domain = next(d for d in domains.list() if d.name == 'nr99-data')
        vms = system.vms_service().list(search='name=nr99-workload')
        if len(vms) != 1:
            raise RuntimeError('Unexpected workload inventory')
        hosts = system.hosts_service()
        selected = hosts.list(search='cluster=nr99')
        if {h.name: h.address for h in selected} != {f'nr99-node{n}': f'node{n}.nr99.test' for n in (1, 2, 3)}:
            raise RuntimeError('Unexpected cluster host inventory')
        if args.action in ('maintain-hosts', 'activate-hosts'):
            if vms[0].status.value != 'down' or domain.status.value != 'maintenance':
                raise RuntimeError('Workload and storage must be quiescent')
            target = 'maintenance' if args.action == 'maintain-hosts' else 'up'
            for host in selected:
                service = hosts.host_service(host.id)
                current = service.get().status.value
                if target == 'maintenance' and current != target:
                    service.deactivate()
                elif target == 'up' and current == 'maintenance':
                    service.activate()
                for _ in range(90):
                    if service.get().status.value == target:
                        break
                    time.sleep(2)
                else:
                    raise RuntimeError('Host did not reach ' + target)
            selected = hosts.list(search='cluster=nr99')
        print(json.dumps({'vm': system.vms_service().vm_service(vms[0].id).get().status.value,
                          'storage': domains.storage_domain_service(domain.id).get().status.value,
                          'hosts': {h.name: h.status.value for h in selected}}))


if __name__ == '__main__':
    main()
