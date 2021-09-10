#!/bin/sh
mkdir /etc/local
mkdir /etc/local/runonce.d
mkdir /etc/local/runonce.d/ran
mv /prov/northrack/runonce.sh /usr/local/bin/runonce.sh
chmod +x /usr/local/bin/runonce.sh
echo "@reboot root /usr/local/bin/runonce.sh" >> /etc/crontab
# mv /prov/northrack/pausenreboot.sh /etc/local/runonce.d/pausenreboot.sh
# chmod +x /etc/local/runonce.d/pausenreboot.sh
