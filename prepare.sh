#!/bin/sh
mkdir /etc/local
mkdir /etc/local/runonce.d
mkdir /etc/local/runonce.d/ran
mv /prov/northrack/runonce.sh /usr/local/bin/runonce.sh
chmod +x /usr/local/bin/runonce.sh
echo "@reboot root sleep 120 && /usr/local/bin/runonce.sh" >> /etc/crontab
mv /prov/northrack/northrack-ansible.sh /etc/local/runonce.d/northrack-ansible.sh
chmod +x /etc/local/runonce.d/northrack-ansible.sh
