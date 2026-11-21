#!/usr/bin/env python3
"""Create the isolated nr99 data center/cluster and enroll only its three hosts."""
import argparse
import json
import time
import ovirtsdk4.types as types
from engine_api import connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hosts', nargs='+', choices=['node1', 'node2', 'node3'], required=True)
    args = parser.parse_args()
    with connect() as connection:
        system = connection.system_service()
        centers = system.data_centers_service()
        found = centers.list(search='name=nr99')
        dc = found[0] if found else centers.add(types.DataCenter(
            name='nr99', description='Disposable north31 Rocky 9.9 acceptance lab', local=False,
            version=types.Version(major=4, minor=8)))
        clusters = system.clusters_service()
        found = clusters.list(search='name=nr99')
        cluster = found[0] if found else clusters.add(types.Cluster(
            name='nr99', description='Replica-3 Gluster and nested KVM lab', data_center=types.DataCenter(id=dc.id),
            cpu=types.Cpu(type='Intel Broadwell Family', architecture=types.Architecture.X86_64),
            version=types.Version(major=4, minor=8), switch_type=types.SwitchType.LEGACY,
            firewall_type=types.FirewallType.FIREWALLD, virt_service=True, gluster_service=True))
        hosts = system.hosts_service()
        ids = {}
        for name in args.hosts:
            existing = hosts.list(search='name=nr99-' + name)
            if existing:
                host = existing[0]
                if host.address != name + '.nr99.test' or host.cluster.id != cluster.id:
                    raise RuntimeError('Existing host does not match the isolated lab definition')
            else:
                host = hosts.add(types.Host(
                    name='nr99-' + name, address=name + '.nr99.test', cluster=types.Cluster(id=cluster.id),
                    ssh=types.Ssh(authentication_method=types.SshAuthenticationMethod.PUBLICKEY, user=types.User(user_name='root')),
                    power_management=types.PowerManagement(enabled=False)),
                    deploy_hosted_engine=False, reboot=False, activate=True)
            ids[name] = host.id
        deadline = time.monotonic() + 900
        last = None
        while time.monotonic() < deadline:
            statuses = {name: hosts.host_service(identifier).get().status.value for name, identifier in ids.items()}
            if statuses != last:
                print(json.dumps({'hosts': statuses}), flush=True)
                last = statuses
            if all(value == 'up' for value in statuses.values()):
                print(json.dumps({'data_center': dc.id, 'cluster': cluster.id, 'hosts': ids, 'passed': True}), flush=True)
                return
            if any(value in ('install_failed', 'non_operational') for value in statuses.values()):
                raise RuntimeError('Host enrollment requires investigation; existing lab objects retained')
            time.sleep(5)
        raise RuntimeError('Timed out waiting for enrolled lab hosts')


if __name__ == '__main__':
    main()
