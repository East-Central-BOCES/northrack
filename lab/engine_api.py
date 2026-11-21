#!/usr/bin/env python3
"""Private-credential API connection for the independent nr99 Engine only."""
from pathlib import Path
import socket


def connect():
    import ovirtsdk4
    if socket.gethostname() not in ('engine', 'engine.nr99.test'):
        raise RuntimeError('This API helper is restricted to the nr99 Engine VM')
    return ovirtsdk4.Connection(
        url='https://engine.nr99.test/ovirt-engine/api',
        username='admin@ovirt@internalsso',
        password=Path('/root/nr99-engine-secrets/admin-password').read_text().strip(),
        ca_file='/etc/pki/ovirt-engine/ca.pem',
        insecure=False,
        timeout=120,
        debug=False,
    )


if __name__ == '__main__':
    import json
    with connect() as connection:
        system = connection.system_service()
        info = system.get()
        print(json.dumps({'product': info.product_info.name, 'clusters': len(system.clusters_service().list()),
                          'hosts': len(system.hosts_service().list()), 'authenticated': True}))
