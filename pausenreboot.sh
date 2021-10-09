#!/bin/sh
logger -n 10.10.10.10 "Launching pausenreboot.sh"
mv /prov/northrack/phase1.sh /etc/local/runonce.d/phase1.sh
chmod +x /etc/local/runonce.d/phase1.sh
logger -n 10.10.10.10 "Moved phase1.sh Pausing..."
sleep 180
logger -n 10.10.10.10 "Rebooting from pausenreboot.sh"
shutdown -r now
