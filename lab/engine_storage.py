#!/usr/bin/env python3
"""Attach only nr99-data as the isolated Engine's Gluster storage domain."""
import json
import time
import ovirtsdk4.types as types
from engine_api import connect


def main():
    with connect() as connection:
        system = connection.system_service()
        dc = system.data_centers_service().list(search='name=nr99')[0]
        host = system.hosts_service().list(search='name=nr99-node1')[0]
        if host.status != types.HostStatus.UP:
            raise RuntimeError('The first lab host must be up')
        domains = system.storage_domains_service()
        existing = domains.list(search='name=nr99-data')
        domain = existing[0] if existing else domains.add(types.StorageDomain(
            name='nr99-data', description='Disposable replica-3 XFS-backed Gluster acceptance domain',
            type=types.StorageDomainType.DATA, host=types.Host(id=host.id),
            storage=types.HostStorage(type=types.StorageType.GLUSTERFS,
                                      vfs_type='glusterfs',
                                      address='172.31.232.11', path='/nr99-data',
                                      mount_options='backup-volfile-servers=172.31.232.12:172.31.232.13'),
            storage_format=types.StorageFormat.V5))
        actual = domains.storage_domain_service(domain.id).get()
        if actual.storage.type != types.StorageType.GLUSTERFS or actual.storage.path != '/nr99-data' or actual.storage.address != '172.31.232.11':
            raise RuntimeError('Existing storage domain does not match the lab definition')
        attached = system.data_centers_service().data_center_service(dc.id).storage_domains_service()
        if not any(item.id == domain.id for item in attached.list()):
            attached.add(types.StorageDomain(id=domain.id))
        service = attached.storage_domain_service(domain.id)
        for attempt in range(90):
            status = service.get().status
            if status == types.StorageDomainStatus.ACTIVE:
                print(json.dumps({'storage_domain': domain.id, 'status': status.value, 'passed': True}))
                return
            if status in (types.StorageDomainStatus.MAINTENANCE, types.StorageDomainStatus.INACTIVE):
                service.activate()
            time.sleep(2)
        raise RuntimeError('Storage domain did not become active')


if __name__ == '__main__':
    main()
