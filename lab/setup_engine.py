#!/usr/bin/env python3
"""Initialize only the independent nr99 test Engine; keep passwords on the VM."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rpm-set', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() or socket.gethostname() not in ('engine', 'engine.nr99.test'):
        raise SystemExit('This helper is restricted to the independent nr99 Engine VM')
    state = json.loads(Path('/var/lib/northrack/provisioned.json').read_text())
    if state['role'] != 'engine':
        raise SystemExit('Engine package preparation has not passed')
    os.umask(0o077)
    private = Path('/root/nr99-engine-secrets')
    private.mkdir(mode=0o700, exist_ok=True)
    password_file = private / 'admin-password'
    if not password_file.exists():
        password_file.write_text(secrets.token_hex(24) + '\n')
    password = password_file.read_text().strip()
    answers = private / 'answers.conf'
    answers.write_text('\n'.join([
        '[environment:default]',
        'OVESETUP_ENGINE_CORE/enable=bool:True',
        'OVESETUP_CONFIG/fqdn=str:engine.nr99.test',
        'OVESETUP_CONFIG/adminPassword=str:' + password,
        'OVESETUP_CONFIG/keycloakAdminPasswd=str:' + password,
        'OVESETUP_PKI/organization=str:NorthRackAcceptance',
        'OVESETUP_GRAFANA_CORE/enable=bool:False',
        'OVESETUP_OVN/ovirtProviderOvn=bool:False',
        'OVESETUP_CL_DB/enable=bool:False',
        '',
    ]))
    completed = private / 'setup.complete'
    if not completed.exists():
        repo_dir = Path('/etc/yum.repos.d')
        saved_repos = private / 'repositories-before-setup'
        if saved_repos.exists():
            raise SystemExit('An interrupted setup left saved repositories; recover the pre-setup checkpoint before retrying')
        repo_dir.rename(saved_repos)
        try:
            repo_dir.mkdir(mode=0o755)
            (repo_dir / 'nr99-accepted.repo').write_text(
                '[northrack-accepted]\nname=Accepted Engine package set\n'
                f'baseurl=file://{args.rpm_set.resolve()}/rpms\nenabled=1\ngpgcheck=1\nskip_if_unavailable=0\n'
            )
            with (private / 'setup-output.log').open('w') as output:
                result = subprocess.run(['engine-setup', '--offline', '--accept-defaults', '--config-append=' + str(answers)],
                                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        finally:
            if repo_dir.exists():
                repo_dir.rename(private / ('repositories-used-during-setup-' + str(time.time_ns())))
            saved_repos.rename(repo_dir)
        if result.returncode:
            print(json.dumps({'setup_exit_code': result.returncode, 'private_log': str(private / 'setup-output.log')}))
            return result.returncode
        completed.write_text('engine-setup completed successfully\n')
    ca = Path('/etc/pki/ovirt-engine/ca.pem')
    context = ssl.create_default_context(cafile=str(ca))
    for _ in range(60):
        try:
            with urllib.request.urlopen('https://engine.nr99.test/ovirt-engine/api', context=context, timeout=5) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2)
            continue
        if status in (200, 401):
            print(json.dumps({'engine': 'engine.nr99.test', 'api_http_status': status, 'tls_verified': True,
                              'private_credentials': str(password_file), 'passed': True}))
            return 0
        time.sleep(2)
    raise SystemExit('Engine API did not become ready; private setup logs retained')


if __name__ == '__main__':
    raise SystemExit(main())
