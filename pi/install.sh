#!/bin/bash
# Run once on the Pi, as root:  sudo bash install.sh
# Raspberry Pi OS Lite (Bookworm or later), any Pi with HDMI.
set -e
cd "$(dirname "$0")"

apt-get update
apt-get install -y python3-pil python3-requests python3-numpy fonts-dejavu-core comitup

# A later run only needs a reboot if it changes the boot settings.
NEED_REBOOT=no
[ -f /etc/systemd/system/tubeboard.service ] || NEED_REBOOT=yes

install -d /opt/tubeboard
cp board.py portal.py /opt/tubeboard/
[ -f /opt/tubeboard/settings.json ] || cp settings.json /opt/tubeboard/
cp tubeboard.service tubeboard-portal.service /etc/systemd/system/

# Force 1080p over HDMI even if the screen is off at boot, never blank the
# console, hide the text cursor. (KMS reads these from the kernel command line.)
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
CMD_WAS=$(cat "$CMD")
# loglevel=3 and logo.nologo stop the kernel drawing its own messages and the
# raspberry logos over the board on the same framebuffer.
# MR: CVT timings, reduced blanking. Without them the kernel builds GTF timings at
# 172.8 MHz, the Pi 3's HDMI block stops at 162 MHz, the mode is thrown away, and a
# monitor that was asleep at boot gives a stretched 1024x768 board.
# Strip any old value for each key first: grep can only add an option, never correct
# one, so a re-run would leave two video= tokens on the line.
for opt in "video=HDMI-A-1:1920x1080MR@60D" "consoleblank=0" "vt.global_cursor_default=0" \
           "logo.nologo" "loglevel=3" "quiet"; do
  key=${opt%%=*}
  if [ "$key" != "$opt" ]; then
    esc=${key//./\\.}                                      # the dot in vt.global_... is a regex
    sed -i "1s/[[:space:]]${esc}=[^[:space:]]*//g" "$CMD"   # drop any old value for this key
    sed -i "1s/\$/ $opt/" "$CMD"
  else
    head -n 1 "$CMD" | grep -qw -- "$opt" || sed -i "1s/\$/ $opt/" "$CMD"
  fi
done

# Kernel and systemd messages go to a VT nobody looks at, so an under-voltage
# warning cannot paint over the board. Alt+F2 still gives a rescue login.
if head -n 1 "$CMD" | grep -q -- 'console=tty1'; then
  sed -i "1s/console=tty1/console=tty3/" "$CMD"
elif ! head -n 1 "$CMD" | grep -q -- 'console=tty[0-9]'; then
  sed -i "1s/\$/ console=tty3/" "$CMD"
fi
[ "$(cat "$CMD")" = "$CMD_WAS" ] || NEED_REBOOT=yes

CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
grep -q '^hdmi_force_hotplug' "$CFG" || {
  printf '\n# tube board\nhdmi_force_hotplug=1\ndisable_overscan=1\n' >> "$CFG"
  NEED_REBOOT=yes
}

# No login prompt drawing over the board, from now and not only after the reboot.
systemctl disable --now getty@tty1.service || true

# Hotspot fallback: if the Pi cannot find a known WiFi it starts "TubeBoard-setup",
# and a phone that joins gets a page to enter the new WiFi name and password.
if [ -f /etc/comitup.conf ]; then
  sed -i 's/^#\? *ap_name:.*/ap_name: TubeBoard-setup/' /etc/comitup.conf
  grep -q '^ap_name:' /etc/comitup.conf || echo 'ap_name: TubeBoard-setup' >> /etc/comitup.conf
fi

systemctl daemon-reload
systemctl enable tubeboard.service tubeboard-portal.service
# restart, not "enable --now": on a second run the units are already running the old
# code, and nothing here would replace it.
systemctl restart tubeboard.service tubeboard-portal.service
echo
echo "Installed. Settings page: http://$(hostname).local:8080  (or http://$(hostname -I | awk '{print $1}'):8080)"
if [ "$NEED_REBOOT" = yes ]; then
  echo "Reboot once so the HDMI settings take effect:  sudo reboot"
fi
