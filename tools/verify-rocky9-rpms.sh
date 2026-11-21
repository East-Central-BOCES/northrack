#!/usr/bin/env bash
# Download the complete role package set, verify every signature, and retain evidence.
set -Eeuo pipefail
export LANG=C.UTF-8
trap 'echo "RPM acceptance failed at line $LINENO; no acceptance marker written." >&2' ERR
role=${1:?Usage: verify-rocky9-rpms.sh host|engine OUTPUT_DIRECTORY}
output=${2:?An absolute output directory is required}
[[ "$role" == host || "$role" == engine ]] || exit 2
[[ "$output" == /* && "$output" != / ]] || exit 2
(( EUID == 0 )) || { echo 'Run as root inside the acceptance VM.' >&2; exit 2; }
# shellcheck source=/dev/null
source /etc/os-release
[[ "$ID" == rocky && "$VERSION_ID" == 9.9 && "$(uname -m)" == x86_64 ]] || exit 2
[[ ! -e "$output" ]] || { echo 'Use a new evidence directory for each run.' >&2; exit 2; }
install -d -m 755 "$output/rpms"
exec > >(tee "$output/verification.log") 2>&1
dnf -y --setopt=timeout=20 --setopt=retries=2 install dnf-plugins-core epel-release centos-release-gluster11 centos-release-ovirt45 gnupg2 createrepo_c
dnf config-manager --set-enabled crb resilientstorage
dnf config-manager --set-disabled centos-gluster10 centos-gluster11-test
dnf config-manager --setopt='*.gpgcheck=1' --setopt='*.skip_if_unavailable=False' --save
dnf config-manager --dump '*' > "$output/repositories.txt"
rpm -qa | sort > "$output/initial-packages.txt"
install -d "$output/keys"
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
python3 "$script_dir/repository_keys.py" "$output/repositories.txt" > "$output/referenced-keys.txt"
while IFS= read -r key; do
    [[ -f "$key" ]] || { echo "Missing publisher key: $key" >&2; exit 1; }
    # Keys are supplied by the signed OS/release RPMs, not scraped from a keyserver.
    rpm -qf "$key" >> "$output/key-owners.txt"
    cp "$key" "$output/keys/"
    gpg --show-keys --with-colons "$key" >> "$output/key-fingerprints.txt" 2>/dev/null
    rpmkeys --import "$key"
done < "$output/referenced-keys.txt"
if [[ "$role" == host ]]; then
    packages=(ansible-core ansible-collection-ansible-posix cockpit cockpit-ovirt-dashboard firewalld
        gluster-ansible-roles glusterfs-server glusterfs-fuse glusterfs-selinux
        ovirt-host ovirt-hosted-engine-setup sanlock vdsm-gluster)
else
    packages=(ovirt-engine)
fi
printf '%s\n' "${packages[@]}" > "$output/requested-packages.txt"
dnf --setopt=timeout=20 --setopt=retries=2 --setopt=install_weak_deps=False \
    --best download --resolve --alldeps --destdir "$output/rpms" "${packages[@]}"
# The dependency walker above does not account for conditional requirements of
# packages already present in the OS (for example SSSD's NFS id-map provider).
# Also resolve the real installation transaction, without installing workloads.
dnf -y --setopt=timeout=20 --setopt=retries=2 --setopt=install_weak_deps=False \
    --best install --downloadonly --downloaddir "$output/rpms" "${packages[@]}"
python3 - "$output" "$role" <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
files = sorted((root / 'rpms').glob('*.rpm'))
if not files:
    raise SystemExit('No RPMs were downloaded')
manifest = []
failures = []
with (root / 'rpm-signatures.txt').open('w') as evidence:
    for rpm in files:
        check = subprocess.run(['rpmkeys', '--checksig', '--verbose', str(rpm)], capture_output=True, text=True)
        evidence.write(check.stdout + check.stderr)
        lines = check.stdout.splitlines()
        if check.returncode or not any('Signature' in line and line.rstrip().endswith(': OK') for line in lines):
            failures.append(rpm.name)
        metadata = subprocess.check_output(['rpm', '-qp', '--qf', '%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE} %{ARCH}', str(rpm)], text=True, stderr=subprocess.DEVNULL)
        digest = hashlib.sha256()
        with rpm.open('rb') as content:
            for chunk in iter(lambda: content.read(1024 * 1024), b''):
                digest.update(chunk)
        manifest.append({'filename': rpm.name, 'nevra': metadata, 'sha256': digest.hexdigest()})
keys = [
    {'filename': key.name, 'sha256': hashlib.sha256(key.read_bytes()).hexdigest()}
    for key in sorted((root / 'keys').iterdir())
]
(root / 'manifest.json').write_text(json.dumps({'role': sys.argv[2], 'packages': manifest, 'keys': keys, 'signature_failures': failures}, indent=2) + '\n')
if failures:
    raise SystemExit('Rejected unsigned or unverifiable RPMs: ' + ', '.join(failures))
print(f'All {len(files)} RPM signatures verified')
PY
createrepo_c "$output/rpms"
printf '%s\n' "$(sha256sum "$output/manifest.json" | cut -d' ' -f1)" > "$output/ACCEPTED"
echo "ACCEPTED $role package set: $output"
