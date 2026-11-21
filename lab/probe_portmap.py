#!/usr/bin/env python3
"""Read Gluster's published brick ports through its documented XDR protocol."""
import json
import secrets
import socket
import struct


def port(host, brick):
    xid = secrets.randbits(32)
    name = brick.encode()
    body = struct.pack('!10I', xid, 0, 2, 34123456, 1, 1, 0, 0, 0, 0)
    body += struct.pack('!I', len(name)) + name + b'\0' * (-len(name) % 4)
    with socket.create_connection((host, 24007), timeout=5) as connection:
        connection.sendall(struct.pack('!I', 0x80000000 | len(body)) + body)
        def read(count):
            result = b''
            while len(result) < count:
                block = connection.recv(count - len(result))
                if not block:
                    raise RuntimeError('Incomplete RPC reply')
                result += block
            return result
        reply = b''
        while True:
            marker, = struct.unpack('!I', read(4))
            if len(reply) + (marker & 0x7fffffff) > 65536:
                raise RuntimeError('Oversized RPC reply')
            reply += read(marker & 0x7fffffff)
            if marker & 0x80000000:
                break
    actual, kind, accepted = struct.unpack('!3I', reply[:12])
    if actual != xid or kind != 1 or accepted != 0:
        raise RuntimeError('RPC request was not accepted')
    _, length = struct.unpack('!2I', reply[12:20])
    offset = 20 + ((length + 3) // 4) * 4
    success, = struct.unpack('!I', reply[offset:offset + 4])
    if success:
        raise RuntimeError('RPC procedure failed: ' + str(success))
    result, error, status, number = struct.unpack('!4i', reply[offset + 4:offset + 20])
    return {'op_ret': result, 'op_errno': error, 'status': status, 'port': number}


def main():
    if socket.gethostname() != 'node1.nr99.test':
        raise SystemExit('Run only on the private nr99 node1 diagnostic guest')
    results = {}
    for node in (1, 2, 3):
        host = f'172.31.232.{10 + node}'
        results[host] = {name: port(host, f'/storage/{disk}/brick/{name}')
                         for name, disk in [('nr99-data', 'sdb1'), ('nr99-io', 'sdc1')]}
    print(json.dumps({'published_portmap': results}, indent=2))


if __name__ == '__main__':
    main()
