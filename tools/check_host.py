#!/usr/bin/env python3
"""Read-only acceptance checks for a provisioned Rocky host."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def command(*args):
    return subprocess.run(args, text=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    release = Path('/etc/os-release').read_text()
    checks = {
        'rocky_9_9': 'ID="rocky"' in release and 'VERSION_ID="9.9"' in release,
        'selinux_enforcing': command('getenforce').stdout.strip() == 'Enforcing',
        'kvm_available': Path('/dev/kvm').is_char_device(),
        'time_synchronized': command('timedatectl', 'show', '-p', 'NTPSynchronized', '--value').stdout.strip() == 'yes',
    }
    ssh = command('/usr/sbin/sshd', '-T', '-C', 'user=root,host=test,addr=172.31.231.1')
    checks['password_login_disabled'] = ssh.returncode == 0 and 'passwordauthentication no' in ssh.stdout.splitlines()
    if config['role'] == 'host':
        for name in ('glusterd', 'firewalld', 'cockpit.socket', 'chronyd'):
            checks['service_' + name] = command('systemctl', 'is-active', name).stdout.strip() == 'active'
        interface = config['storage_interface']
        link = command('ip', '-j', 'link', 'show', interface)
        checks['storage_mtu_9000'] = link.returncode == 0 and json.loads(link.stdout)[0]['mtu'] == 9000
        checks['storage_firewall_zone'] = command('firewall-cmd', '--get-zone-of-interface', interface).stdout.strip() == 'storage'
        checks['gluster_allowed_on_storage'] = command('firewall-cmd', '--zone=storage', '--query-service=glusterfs').stdout.strip() == 'yes'
        checks['gluster_closed_on_public'] = command('firewall-cmd', '--zone=public', '--query-service=glusterfs').stdout.strip() == 'no'
        checks['brick_ports_above_management'] = 'option base-port 55000' in Path('/etc/glusterfs/glusterd.vol').read_text()
        for disk in config['disks']:
            mounted = command('findmnt', '-J', '--mountpoint', disk['mount'], '-o', 'TARGET,FSTYPE,UUID')
            rows = json.loads(mounted.stdout).get('filesystems', []) if mounted.returncode == 0 else []
            checks['mount_' + disk['mount']] = bool(rows and rows[0]['fstype'] == 'xfs' and rows[0].get('uuid'))
            context = command('ls', '-Zd', disk['mount'] + '/brick')
            checks['brick_context_' + disk['mount']] = context.returncode == 0 and 'glusterd_brick_t' in context.stdout
    state = Path('/var/lib/northrack/provisioned.json')
    checks['successful_configuration_recorded'] = state.is_file() and json.loads(state.read_text())['configuration'] == config
    print(json.dumps({'checks': checks, 'passed': all(checks.values())}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
