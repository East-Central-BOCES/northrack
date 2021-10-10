#!/bin/bash
sleep 30
dnf -y install https://resources.ovirt.org/pub/yum-repo/ovirt-release44.rpm
ansible-playbook /prov/northrack/northrack.yml > /etc/local/runonce.d/ran/northrack.log 2>&1
shutdown -r now