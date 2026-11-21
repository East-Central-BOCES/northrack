#!/usr/bin/env python3
"""Compare quiescent replica contents, including VM disk shards, in nr99 only."""
import argparse
import json
from ssh_lab import run

CODE = '''import hashlib,json,os,pathlib,socket
assert socket.gethostname() in ('node1.nr99.test','node2.nr99.test','node3.nr99.test')
result={}
for base,volume in (('/storage/sdb1/brick','nr99-data'),('/storage/sdc1/brick','nr99-io')):
 root=pathlib.Path(base)/volume
 for directory,dirs,files in os.walk(root,followlinks=False):
  dirs[:]=[d for d in dirs if d!='.glusterfs']
  for name in sorted(files):
   p=pathlib.Path(directory)/name; key=volume+'/'+str(p.relative_to(root))
   if p.is_symlink(): result[key]={'symlink':os.readlink(p)}; continue
   digest=hashlib.sha256()
   with p.open('rb') as stream:
    for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
   result[key]={'size':p.stat().st_size,'sha256':digest.hexdigest()}
print(json.dumps(result,sort_keys=True))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--known-hosts', required=True)
    args = parser.parse_args()
    results = {}
    for node in (1, 2, 3):
        results[node] = json.loads(run(node, 'python3', '-c', CODE,
                                      known_hosts=args.known_hosts, timeout=300).stdout)
    paths = set().union(*(set(value) for value in results.values()))
    mismatches = [path for path in sorted(paths)
                  if not results[1].get(path) == results[2].get(path) == results[3].get(path)]
    print(json.dumps({'paths_checked': len(paths), 'replicas': 3,
                      'mismatches': mismatches, 'passed': not mismatches}, indent=2))
    if mismatches:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
