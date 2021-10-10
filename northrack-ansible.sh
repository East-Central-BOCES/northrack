#!/bin/bash
pause 30
ansible-playbook /prov/northrack/northrack.yml > /etc/local/runonce.d/ran/northrack.log 2>&1
shutdown -r now