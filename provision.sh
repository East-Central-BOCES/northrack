#!/usr/bin/env bash
set -Eeuo pipefail
export LANG=C.UTF-8
config='' rpm_set='' manifest_hash='' check=false format=false
while (( $# )); do
    case "$1" in
        --config) config=${2:?}; shift 2 ;;
        --rpm-set) rpm_set=${2:?}; shift 2 ;;
        --manifest-sha256) manifest_hash=${2:?}; shift 2 ;;
        --check) check=true; shift ;;
        --format-data-disks) format=true; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
[[ -n "$config" && -n "$rpm_set" && -n "$manifest_hash" ]] || { echo 'Required: --config FILE --rpm-set DIRECTORY --manifest-sha256 HASH' >&2; exit 2; }
(( EUID == 0 )) || exit 2
# shellcheck source=/dev/null
source /etc/os-release
[[ "$ID" == rocky && "$VERSION_ID" == 9.9 && "$(uname -m)" == x86_64 ]] || { echo 'Only Rocky 9.9 x86_64 is accepted.' >&2; exit 2; }
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
config=$(realpath "$config")
rpm_set=$(realpath "$rpm_set")
[[ "$config" != *$'\n'* && "$rpm_set" != *$'\n'* ]] || exit 2
role=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["role"])' "$config")
python3 "$repo_root/tools/accepted_set.py" "$rpm_set" "$manifest_hash" "$role"
storage_flags=()
$format && storage_flags+=(--format-data-disks)
python3 "$repo_root/tools/storage.py" "$config" "${storage_flags[@]}"
if [[ "$role" == host ]]; then
    python3 "$repo_root/tools/network.py" "$config"
fi
if $check && ! command -v ansible-playbook >/dev/null; then
    echo 'Preflight passed; Ansible bootstrap is not installed. No configuration changes made.'
    exit 0
fi
if ! $check; then
    while IFS= read -r key; do rpmkeys --import "$rpm_set/keys/$key"; done < <(
        python3 -c 'import json,sys; print("\n".join(k["filename"] for k in json.load(open(sys.argv[1]))["keys"]))' "$rpm_set/manifest.json"
    )
    install -d /etc/yum.repos.d
    repo_file=$(mktemp)
    cat > "$repo_file" <<EOF
[northrack-accepted]
name=Verified Northrack package set
baseurl=file://$rpm_set/rpms
enabled=0
gpgcheck=1
skip_if_unavailable=0
EOF
    if ! cmp -s "$repo_file" /etc/yum.repos.d/northrack-accepted.repo; then
        install -m 644 "$repo_file" /etc/yum.repos.d/northrack-accepted.repo
    fi
    rm -f "$repo_file"
    dnf -y --disablerepo='*' --enablerepo=northrack-accepted --setopt=install_weak_deps=False \
        install ansible-core ansible-collection-ansible-posix
fi
args=(--inventory 'localhost,' --connection local "$repo_root/playbooks/rocky9.yml"
    --extra-vars "config_path=$config" --extra-vars "repo_root=$repo_root"
    --extra-vars "rpm_manifest_sha256=$manifest_hash" --extra-vars "format_data_disks=$format")
$check && args+=(--check)
ansible-playbook "${args[@]}"
