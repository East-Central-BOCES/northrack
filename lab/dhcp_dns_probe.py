#!/usr/bin/env python3
"""Bounded fresh DHCP/DNS probe using a temporary macvlan namespace on north31."""
import argparse
import datetime
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import time

COOKIE = b'\x63\x82\x53\x63'
SERVER = '10.11.0.254'
NAMESPACE = 'nr99-dhcp'
INTERFACE = 'nr99dhcp'


def packet(xid, mac, hostname, kind, address=None):
    header = struct.pack('!BBBBIHH4s4s4s4s16s64s128s', 1, 1, 6, 0, xid, 0, 0x8000,
                         b'\0' * 4, b'\0' * 4, b'\0' * 4, b'\0' * 4,
                         mac.ljust(16, b'\0'), b'\0' * 64, b'\0' * 128)
    options = b'\x35\x01' + bytes([kind]) + b'\x3d\x07\x01' + mac
    options += bytes([12, len(hostname)]) + hostname.encode('ascii')
    options += b'\x37\x06\x01\x03\x06\x0f\x33\x36'
    if address:
        options += b'\x32\x04' + socket.inet_aton(address) + b'\x36\x04' + socket.inet_aton(SERVER)
    return header + COOKIE + options + b'\xff'


def parse(data, xid, mac):
    if len(data) < 240 or data[0] != 2 or struct.unpack('!I', data[4:8])[0] != xid or data[28:34] != mac or data[236:240] != COOKIE:
        return None
    options = {}
    index = 240
    while index < len(data):
        code = data[index]
        index += 1
        if code == 255:
            break
        if code == 0:
            continue
        if index >= len(data):
            return None
        length = data[index]
        index += 1
        if index + length > len(data):
            return None
        options[code] = data[index:index + length]
        index += length
    if options.get(54) != socket.inet_aton(SERVER):
        return None
    return socket.inet_ntoa(data[16:20]), options


def client(hostname, mac_text):
    mac = bytes.fromhex(mac_text.replace(':', ''))
    xid = secrets.randbits(32)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, INTERFACE.encode() + b'\0')
        connection.bind(('', 68))
        connection.settimeout(1)

        def exchange(kind, wanted, address=None):
            for attempt in range(3):
                connection.sendto(packet(xid, mac, hostname, kind, address), ('255.255.255.255', 67))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        data, _ = connection.recvfrom(4096)
                    except socket.timeout:
                        continue
                    reply = parse(data, xid, mac)
                    if reply and reply[1].get(53) == bytes([wanted]):
                        return reply
            raise RuntimeError('No expected DHCP response from the MAAS server')

        address, _ = exchange(1, 2)
        address, options = exchange(3, 5, address)
        if ipaddress.ip_address(address) not in ipaddress.ip_network('10.11.0.0/24'):
            raise RuntimeError('Unexpected lease subnet')
        print(json.dumps({'address': address, 'server': SERVER, 'hostname': hostname,
                          'mac': mac_text, 'lease_seconds': int.from_bytes(options.get(51, b''), 'big')}))


def release(address, mac_text):
    mac = bytes.fromhex(mac_text.replace(':', ''))
    data = bytearray(packet(secrets.randbits(32), mac, 'nr99-release', 7))
    data[10:12] = b'\0\0'
    data[12:16] = socket.inet_aton(address)
    data[-1:] = b'\x36\x04' + socket.inet_aton(SERVER) + b'\xff'
    subprocess.run(['ip', 'address', 'add', address + '/24', 'dev', INTERFACE], check=True)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, INTERFACE.encode() + b'\0')
        connection.bind((address, 68))
        connection.sendto(data, (SERVER, 67))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', nargs=2, metavar=('HOSTNAME', 'MAC'))
    parser.add_argument('--release', nargs=2, metavar=('IP', 'MAC'))
    args = parser.parse_args()
    if os.geteuid() or socket.gethostname().split('.')[0] != 'north31':
        raise SystemExit('Run as root on the approved north31 test host only')
    if args.client:
        return client(*args.client)
    if args.release:
        return release(*args.release)

    def command(*argv, **kwargs):
        return subprocess.run(argv, text=True, capture_output=True, check=True, **kwargs)

    interfaces = json.loads(command('ip', '-j', 'address', 'show', 'eno1').stdout)
    if not any(a.get('local') == '10.11.0.23' for a in interfaces[0]['addr_info']):
        raise RuntimeError('north31 management interface does not match the recorded baseline')
    if NAMESPACE in command('ip', 'netns', 'list').stdout.split():
        raise RuntimeError('The probe namespace already exists; inspect before retrying')
    hostname = 'nr99-dns-' + datetime.datetime.now(datetime.timezone.utc).strftime('%m%d%H%M%S')
    mac = '52:54:00:99:05:' + secrets.token_hex(1)
    lease = None
    command('ip', 'netns', 'add', NAMESPACE)
    try:
        command('ip', 'link', 'add', INTERFACE, 'link', 'eno1', 'address', mac, 'type', 'macvlan', 'mode', 'bridge')
        command('ip', 'link', 'set', INTERFACE, 'netns', NAMESPACE)
        command('ip', '-n', NAMESPACE, 'link', 'set', 'lo', 'up')
        command('ip', '-n', NAMESPACE, 'link', 'set', INTERFACE, 'up')
        command('ip', '-n', NAMESPACE, 'route', 'add', '255.255.255.255/32', 'dev', INTERFACE)
        lease = json.loads(command('ip', 'netns', 'exec', NAMESPACE, 'python3', str(Path(__file__).resolve()),
                                  '--client', hostname, mac, timeout=40).stdout)
        print(json.dumps({'dhcp_ack': lease}), flush=True)
        started = time.monotonic()
        passed = False
        for attempt in range(13):
            reply = command('dig', '@10.10.10.162', hostname + '.maas', 'A', '+time=2', '+tries=1', '+noall', '+answer', '+comments').stdout
            authoritative = ' aa;' in reply or ' aa ' in reply
            addresses = [line.split()[-1] for line in reply.splitlines() if line and not line.startswith(';') and '\tA\t' in line]
            if authoritative and lease['address'] in addresses:
                passed = True
                break
            if attempt < 12:
                time.sleep(10)
        print(json.dumps({'fresh_dhcp_dns': {'hostname': hostname + '.maas', 'expected_address': lease['address'],
                                            'authoritative': authoritative, 'answers': addresses,
                                            'elapsed_seconds': round(time.monotonic() - started, 1),
                                            'passed': passed}}), flush=True)
        if not passed:
            raise RuntimeError('Fresh lease hostname was not published to authoritative DNS; MAAS gate must stop')
    finally:
        try:
            if lease:
                result = subprocess.run(['ip', 'netns', 'exec', NAMESPACE, 'python3', str(Path(__file__).resolve()),
                                         '--release', lease['address'], mac], capture_output=True, timeout=10)
                print(json.dumps({'dhcp_release_sent': result.returncode == 0}), flush=True)
        finally:
            subprocess.run(['ip', 'netns', 'delete', NAMESPACE], check=True)
            print(json.dumps({'temporary_namespace_removed': True}), flush=True)


if __name__ == '__main__':
    main()
