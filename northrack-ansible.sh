#!/bin/bash
sleep 30
{
  dnf -y install https://resources.ovirt.org/pub/yum-repo/ovirt-release44.rpm
  dnf -y install sanlock
  dnf -y install ovirt-hosted-engine-setup
  dnf -y install cockpit-ovirt-dashboard
  dnf -y install vdsm-gluster
  dnf -y install ovirt-host
  dnf -y install gluster-ansible-roles
  ansible-playbook /prov/northrack/northrack.yml
  firewall-cmd --add-service=glusterfs --permanent
  firewall-cmd --reload
} >> /etc/local/runonce.d/ran/northrack.log 2>&1
shutdown -r now