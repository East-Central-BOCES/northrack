#!/bin/sh
mkdir /etc/local
mkdir /etc/local/runonce.d
mkdir /etc/local/runonce.d/ran
mv /prov/northrack/runonce.sh /usr/local/bin/runonce.sh
echo "@reboot root /usr/local/bin/runonce.sh"
mv /prov/northrack/northrack-anisble.sh /etc/local/runonce.d/northrack-ansible.sh
