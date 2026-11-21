#!/usr/bin/env python3
"""Create only the named, isolated nr99 lab guests; never touch physical disks."""
import argparse
import base64
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path('/var/lib/libvirt/images/nr99')
ISO = Path('/var/lib/libvirt/boot/nr99/Rocky-9.9-minimal.iso')
ISO_SHA = '56772c6c07464ea8e55b64cb5ec2448d16e1b4673b8041678dacfd540131401c'
NODES = {'engine': 10, 'node1': 11, 'node2': 12, 'node3': 13}


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def management_xml():
    network = ET.Element('network')
    ET.SubElement(network, 'name').text = 'nr99-mgmt'
    ET.SubElement(network, 'forward', mode='nat')
    ET.SubElement(network, 'bridge', name='nr99mgmt', stp='on', delay='0')
    ET.SubElement(network, 'domain', name='nr99.test', localOnly='yes')
    dns = ET.SubElement(network, 'dns')
    for name, number in NODES.items():
        host = ET.SubElement(dns, 'host', ip=f'172.31.231.{number}')
        ET.SubElement(host, 'hostname').text = f'{name}.nr99.test'
        ET.SubElement(host, 'hostname').text = name
    ip = ET.SubElement(network, 'ip', address='172.31.231.1', netmask='255.255.255.0')
    dhcp = ET.SubElement(ip, 'dhcp')
    ET.SubElement(dhcp, 'range', start='172.31.231.100', end='172.31.231.199')
    return ET.tostring(network, encoding='unicode')


def storage_xml():
    return '<network><name>nr99-storage</name><mtu size="9000"/><bridge name="nr99store" stp="on" delay="0"/></network>'


def kickstart(name, key):
    if not key.startswith(('ssh-rsa ', 'ssh-ed25519 ', 'ecdsa-sha2-')) or '\n' in key:
        raise ValueError('Expected one SSH public key, not a private key or multiple lines')
    algorithm, encoded, *_ = key.split()
    base64.b64decode(encoded, validate=True)
    key = algorithm + ' ' + encoded
    number = NODES[name]
    storage = '' if name == 'engine' else (
        f'network --device=eth1 --vlanid=106 --interfacename=storage106 '
        f'--bootproto=static --ip=172.31.232.{number} --netmask=255.255.255.0 '
        '--onboot=yes --mtu=9000 --nodefroute --noipv6\n'
    )
    storage_post = '' if name == 'engine' else '''python3 - <<'NETWORK_PROFILES'
import configparser
from pathlib import Path
for name in ('eth1', 'storage106'):
    path = Path('/etc/NetworkManager/system-connections') / (name + '.nmconnection')
    config = configparser.ConfigParser(interpolation=None)
    config.read(path)
    config['connection']['autoconnect'] = 'true'
    if 'ethernet' not in config:
        config['ethernet'] = {}
    config['ethernet']['mtu'] = '9000'
    config['ipv6']['method'] = 'disabled'
    if name == 'eth1':
        config['ipv4'].clear()
        config['ipv4']['method'] = 'disabled'
    with path.open('w') as target:
        config.write(target)
    path.chmod(0o600)
NETWORK_PROFILES
'''
    return f'''text
cdrom
lang en_US.UTF-8
keyboard us
timezone America/Denver --utc
rootpw --lock
user --name=jarredm --groups=wheel --lock
sshkey --username=jarredm "{key}"
firewall --enabled --service=ssh
selinux --enforcing
network --device=eth0 --bootproto=static --ip=172.31.231.{number} --netmask=255.255.255.0 --gateway=172.31.231.1 --nameserver=172.31.231.1 --hostname={name}.nr99.test --onboot=yes --noipv6
{storage}ignoredisk --only-use=vda
zerombr
clearpart --all --initlabel --drives=vda
autopart --type=plain --fstype=xfs --nohome --noswap
bootloader --location=mbr --append="net.ifnames=0 biosdevname=0"
reboot
%packages
@^minimal-environment
openssh-server
sudo
chrony
python3
curl
%end
%post --erroronfail --log=/root/nr99-kickstart.log
printf 'jarredm ALL=(ALL) NOPASSWD:ALL\\n' > /etc/sudoers.d/nr99-admin
chmod 440 /etc/sudoers.d/nr99-admin
printf 'PermitRootLogin no\\nPasswordAuthentication no\\nKbdInteractiveAuthentication no\\n' > /etc/ssh/sshd_config.d/00-nr99.conf
{storage_post}
systemctl enable sshd chronyd
%end
'''


def ensure_network(name, xml):
    existing = run('virsh', 'net-dumpxml', name, check=False)
    if existing.returncode == 0:
        tree = ET.fromstring(existing.stdout)
        bridge = tree.find('bridge')
        if bridge is None or bridge.get('name') != ('nr99mgmt' if name == 'nr99-mgmt' else 'nr99store'):
            raise RuntimeError(f'Refusing to reuse unexpected network {name}')
        if name == 'nr99-mgmt' and tree.find('ip').get('address') != '172.31.231.1':
            raise RuntimeError('Existing lab subnet does not match')
        if name == 'nr99-storage' and tree.find('forward') is not None:
            raise RuntimeError('Storage network must be isolated')
    else:
        routes = json.loads(run('ip', '-j', 'route', 'show', 'table', 'all').stdout)
        for route in routes:
            dest = route.get('dst', 'default')
            if dest == 'default' or route.get('dev') in ('nr99mgmt', 'nr99store'):
                continue
            existing_net = ipaddress.ip_network(dest, strict=False)
            if existing_net.version == 4 and any(existing_net.overlaps(ipaddress.ip_network(n)) for n in ('172.31.231.0/24', '172.31.232.0/24')):
                raise RuntimeError(f'Lab route overlaps {dest}')
        path = ROOT / f'{name}.xml'
        path.write_text(xml + '\n')
        run('virsh', 'net-define', str(path))
    info = run('virsh', 'net-info', name).stdout
    if 'Active:         yes' not in info:
        run('virsh', 'net-start', name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create', choices=NODES, required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] != 'north31':
        raise SystemExit('This lab helper is restricted to north31')
    if run('virsh', 'dominfo', f'nr99-{args.create}', check=False).returncode == 0:
        raise SystemExit('Guest already exists; never overwrite a lab guest')
    if not ISO.is_file():
        raise SystemExit('Verified installer ISO is missing')
    digest = hashlib.sha256()
    with ISO.open('rb') as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != ISO_SHA:
        raise SystemExit('Installer ISO checksum mismatch')
    key = args.public_key.read_text().strip()
    config = kickstart(args.create, key)
    ROOT.mkdir(parents=True, exist_ok=True)
    ensure_network('nr99-mgmt', management_xml())
    ensure_network('nr99-storage', storage_xml())
    guest_dir = ROOT / args.create
    guest_dir.mkdir(exist_ok=False)
    ks = guest_dir / 'ks.cfg'
    ks.write_text(config)
    number = NODES[args.create]
    engine = args.create == 'engine'
    disks = [('os', 100 if engine else 80)]
    if not engine:
        disks += [('data01', 40), ('data02', 40)]
    cmd = ['virt-install', '--connect', 'qemu:///system', '--name', f'nr99-{args.create}',
           '--memory', '16384', '--vcpus', '4', '--cpu', 'host-passthrough',
           '--os-variant', 'rocky9', '--graphics', 'none', '--noautoconsole',
           '--location', str(ISO), '--initrd-inject', str(ks),
           '--extra-args', 'inst.ks=file:/ks.cfg console=ttyS0,115200n8 net.ifnames=0 biosdevname=0',
           '--network', f'network=nr99-mgmt,model=virtio,mac=52:54:00:99:01:{number:02x}',
           '--serial', f'file,path={guest_dir}/console.log', '--wait', '0']
    if not engine:
        cmd += ['--network', f'network=nr99-storage,model=virtio,mac=52:54:00:99:02:{number:02x}']
    for label, size in disks:
        disk = guest_dir / f'{label}.qcow2'
        cmd += ['--disk', f'path={disk},size={size},format=qcow2,bus=virtio,serial=nr99-{args.create}-{label}']
    result = run(*cmd)
    print(result.stdout)
    print(result.stderr)
    print(f'Created nr99-{args.create}; console log: {guest_dir}/console.log')


if __name__ == '__main__':
    main()
