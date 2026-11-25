#!/usr/bin/env python3
"""Engine-side preparation and status for the operator-paced nr99 tests."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time


def activate_ready(service, address, retry_error, deadline, clock=time.monotonic,
                   sleep=time.sleep, connect=socket.create_connection):
    """Wait for the management listener; retry only the observed network fault."""
    activation_requested = False
    while clock() < deadline:
        state = service.get().status.value
        if state == 'up':
            return
        if state == 'maintenance':
            try:
                with connect((address, 54321), timeout=3):
                    pass
            except OSError:
                sleep(5)
                continue
            try:
                service.activate()
                activation_requested = True
            except retry_error as error:
                if 'Network error during communication with the Host' not in str(error):
                    raise
        elif state not in ('connecting', 'initializing') and not (state == 'unassigned' and activation_requested):
            raise RuntimeError('Unexpected host activation state: ' + state)
        sleep(5)
    raise RuntimeError('Host management readiness/activation deadline exceeded')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['quiesce', 'status', 'maintain-hosts', 'activate-hosts', 'resume', 'resume-paused'])
    args = parser.parse_args()
    if os.geteuid() or socket.gethostname() != 'engine.nr99.test':
        raise SystemExit('Restricted to root on the independent nr99 Engine')
    from engine_api import connect
    import ovirtsdk4
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
        if args.action == 'resume-paused':
            service = system.vms_service().vm_service(vms[0].id)
            if domain.status.value != 'active' or service.get().status.value not in ('paused', 'up'):
                raise RuntimeError('Only the existing paused workload on active storage may be resumed')
            if service.get().status.value == 'paused':
                service.start()
            for _ in range(90):
                if service.get().status.value == 'up':
                    break
                time.sleep(2)
            else:
                raise RuntimeError('Paused workload did not return to UP')
        if args.action in ('maintain-hosts', 'activate-hosts'):
            if vms[0].status.value != 'down' or domain.status.value != 'maintenance':
                raise RuntimeError('Workload and storage must be quiescent')
            target = 'maintenance' if args.action == 'maintain-hosts' else 'up'
            activation_deadline = time.monotonic() + 420
            for host in selected:
                service = hosts.host_service(host.id)
                if target == 'up':
                    activate_ready(service, host.address, ovirtsdk4.Error, activation_deadline)
                    continue
                current = service.get().status.value
                if target == 'maintenance' and current != target:
                    service.deactivate()
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
