#!/bin/bash
sleep 30
ansible-playbook /prov/northrack/phase1.yml > /etc/local/runonce.d/ran/phase1.log 2>&1
dnf -y install glusterfs-server >> /etc/local/runonce.d/ran/phase1.log 2>&1
#mv /prov/northrack/northrack-ansible.sh /etc/local/runonce.d/northrack-ansible.sh
#chmod +x /etc/local/runonce.d/northrack-ansible.sh
shutdown -r now
