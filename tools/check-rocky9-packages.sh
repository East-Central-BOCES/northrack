#!/usr/bin/env bash
# Repository/dependency probe for a disposable Rocky container, never a host installer.
set -Eeuo pipefail
export LANG=C.UTF-8
trap 'echo "Package probe failed at line $LINENO" >&2' ERR

if [[ ! -f /.dockerenv && ! -f /run/.containerenv ]]; then
    echo 'Run this probe inside a disposable container; it installs repository packages.' >&2
    exit 2
fi
if (( EUID != 0 )); then
    echo 'Container root is required.' >&2
    exit 2
fi
# shellcheck source=/dev/null
source /etc/os-release
if [[ "$ID" != rocky || "$VERSION_ID" != 9.9 || "$(uname -m)" != x86_64 ]]; then
    echo 'This acceptance probe requires Rocky Linux 9.9 on x86_64.' >&2
    exit 2
fi

echo "Testing $PRETTY_NAME ($(uname -m))"
dnf -y --setopt=timeout=20 --setopt=retries=2 install \
    dnf-plugins-core epel-release centos-release-gluster11 centos-release-ovirt45
dnf config-manager --set-enabled crb resilientstorage
# oVirt's release RPM also pulls in Gluster 10. Gluster 11's release RPM
# enables its testing repo with signature checks off. Exclude both sources.
dnf config-manager --set-disabled centos-gluster10 centos-gluster11-test
dnf --setopt=timeout=20 --setopt=retries=2 --setopt=skip_if_unavailable=False makecache --refresh

# Use the CLI so Rocky's DNF plugins populate variables such as $stream.
# --assumeno resolves the transaction but aborts before downloads or installation.
transaction_log=$(mktemp /tmp/northrack-transaction.XXXXXX)
transaction_status=0
dnf --setopt=timeout=20 --setopt=retries=2 \
    --setopt=skip_if_unavailable=False --setopt=install_weak_deps=False \
    --best --assumeno install \
    ansible-core cockpit cockpit-ovirt-dashboard firewalld \
    gluster-ansible-roles glusterfs-server glusterfs-fuse glusterfs-selinux \
    ovirt-host ovirt-hosted-engine-setup sanlock vdsm-gluster \
    > "$transaction_log" 2>&1 || transaction_status=$?
cat "$transaction_log"
if (( transaction_status != 1 )) ||
    ! grep -q '^Dependencies resolved\.' "$transaction_log" ||
    ! grep -q '^Transaction Summary' "$transaction_log" ||
    ! grep -q '^Operation aborted\.' "$transaction_log"; then
    echo 'Combined dependency resolution did not produce the expected dry-run transaction.' >&2
    exit 1
fi
echo 'RESOLVED: combined transaction; no workload installation performed.'
echo 'Repository configuration (including signature policy) follows:'
dnf config-manager --dump '*'
