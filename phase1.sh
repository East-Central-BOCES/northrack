#!/bin/sh
pause 30
ansible-playbook /prov/northrack/phase1.yml
#mv /prov/northrack/northrack-ansible.sh /etc/local/runonce.d/northrack-ansible.sh
#chmod +x /etc/local/runonce.d/northrack-ansible.sh
shutdown -r now