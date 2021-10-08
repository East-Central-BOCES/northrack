#!/bin/sh
pause 30
ansible-playbook /prov/northrack/northrack.yml
shutdown -r now