#!/bin/sh
logger -n 10.10.10.10 "Launching pausenreboot.sh"
mv /prov/northrack/northrack-ansible.sh /etc/local/runonce.d/northrack-ansible.sh
chmod +x /etc/local/runonce.d/northrack-ansible.sh
logger -n 10.10.10.10 "Moved northrack-ansible.sh Pausing..."
sleep 180
logger -n 10.10.10.10 "Rebooting from pausenreboot.sh"
shutdown -r now
