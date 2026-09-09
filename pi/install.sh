#!/bin/bash
# Run once on the Pi, as root:  sudo bash install.sh
# Raspberry Pi OS Lite (Bookworm or later), any Pi with HDMI.
set -e
cd "$(dirname "$0")"

apt-get update
apt-get install -y python3-pil python3-requests fonts-dejavu-core comitup

install -d /opt/tubeboard
cp board.py portal.py /opt/tubeboard/
[ -f /opt/tubeboard/settings.json ] || cp settings.json /opt/tubeboard/
cp tubeboard.service tubeboard-portal.service /etc/systemd/system/

# Force 1080p over HDMI even if the screen is off at boot, never blank the
# console, hide the text cursor. (KMS reads these from the kernel command line.)
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
for opt in "video=HDMI-A-1:1920x1080@60D" "consoleblank=0" "vt.global_cursor_default=0" "quiet"; do
  grep -q -- "$opt" "$CMD" || sed -i "s/\$/ $opt/" "$CMD"
done
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
grep -q '^hdmi_force_hotplug' "$CFG" || printf '\n# tube board\nhdmi_force_hotplug=1\ndisable_overscan=1\n' >> "$CFG"

# No login prompt drawing over the board.
systemctl disable getty@tty1.service || true

# Hotspot fallback: if the Pi cannot find a known WiFi it starts "TubeBoard-setup",
# and a phone that joins gets a page to enter the new WiFi name and password.
if [ -f /etc/comitup.conf ]; then
  sed -i 's/^#\? *ap_name:.*/ap_name: TubeBoard-setup/' /etc/comitup.conf
  grep -q '^ap_name:' /etc/comitup.conf || echo 'ap_name: TubeBoard-setup' >> /etc/comitup.conf
fi

systemctl daemon-reload
systemctl enable --now tubeboard.service tubeboard-portal.service
echo
echo "Installed. Settings page: http://$(hostname).local  (or http://$(hostname -I | awk '{print $1}'))"
echo "Reboot once so the HDMI settings take effect:  sudo reboot"
