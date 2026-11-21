#!/usr/bin/env python3
"""Install and exercise the independent Engine's site firewall hook."""
import os
from pathlib import Path
import socket
import subprocess
import tempfile


def main():
    if os.geteuid() or socket.gethostname() not in ('engine', 'engine.nr99.test'):
        raise SystemExit('Run as root on the independent nr99 Engine only')
    source = Path(__file__).with_name('ovirt-host-deploy-post-tasks.yml')
    target = Path('/etc/ovirt-engine/ansible/ovirt-host-deploy-post-tasks.yml')
    if target.exists() and target.read_bytes() != source.read_bytes():
        raise SystemExit('Refusing to overwrite an existing site deployment hook')
    target.write_bytes(source.read_bytes())
    target.chmod(0o644)
    subprocess.run(['restorecon', str(target)], check=True)
    play = ('- hosts: all\n  gather_facts: false\n'
            '  vars:\n    host_deploy_cluster_name: nr99\n'
            '  tasks:\n    - ansible.builtin.include_tasks: ' + str(target) + '\n')
    with tempfile.TemporaryDirectory(prefix='nr99-firewall-') as directory:
        wrapper = Path(directory) / 'apply.yml'
        wrapper.write_text(play)
        subprocess.run(['ansible-playbook', '-i',
                        'node1.nr99.test,node2.nr99.test,node3.nr99.test,',
                        '-u', 'root', '--private-key', '/etc/pki/ovirt-engine/keys/engine_id_rsa',
                        '--ssh-common-args=-o StrictHostKeyChecking=accept-new', str(wrapper)], check=True)


if __name__ == '__main__':
    main()
