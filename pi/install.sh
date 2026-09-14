#!/bin/bash
# Run once on the Pi, as root:  sudo bash install.sh
# Raspberry Pi OS Lite (Bookworm or later), any Pi with HDMI.
set -e
cd "$(dirname "$0")"

apt-get update
# comitup takes over NetworkManager, and on a headless box that can drop the
# WiFi you are connected over. Install the board first, add the hotspot second,
# with a screen attached:  SKIP_COMITUP=1 bash install.sh
# ddcutil drives the monitor's brightness over the HDMI cable, which is the
# only way to change it once the screen is sealed in the frame.
PKGS="python3-pil python3-requests python3-numpy fonts-dejavu-core ddcutil"
[ -n "$SKIP_COMITUP" ] || PKGS="$PKGS comitup"
apt-get install -y $PKGS

# A later run only needs a reboot if it changes the boot settings.
NEED_REBOOT=no
[ -f /etc/systemd/system/tubeboard.service ] || NEED_REBOOT=yes

install -d /opt/tubeboard
cp board.py portal.py screen.py /opt/tubeboard/
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
# No bpp suffix on the video= line: a Pi 3 gives 16-bit whatever you ask for
# (the vc4 fbdev emulation decides), so -32 would only look like it did something.
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

# Pin the screen's own EDID. Without this, cutting power to the MONITOR (a
# blip, or someone switching the wall socket) makes the Pi re-ask the screen who
# it is, get no answer in time, and fall back to a generic list that stops at
# 1024x768 - while the framebuffer stays 1920x1080. The result is the board
# drawn at full size and displayed zoomed into its top-left corner, and it stays
# that way until a reboot. Verified on the bench 2026-09-14, both ways round.
# Capture happens on the first install, while the screen is awake and talking.
EDID=/lib/firmware/edid/tubeboard.bin
SRC=/sys/class/drm/card0-HDMI-A-1/edid
# NB: sysfs reports this file as zero bytes even when it has content, so
# [ -s "$SRC" ] is always false here. Copy it, then check what we actually got.
if [ ! -s "$EDID" ] && [ -r "$SRC" ]; then
  mkdir -p /lib/firmware/edid
  cp "$SRC" "$EDID" 2>/dev/null || true
  if [ -s "$EDID" ]; then
    echo "saved this screen's EDID ($(wc -c < "$EDID") bytes)"
  else
    rm -f "$EDID"
  fi
fi
if [ -s "$EDID" ]; then
  sed -i "1s| drm.edid_firmware=[^ ]*||g" "$CMD"
  sed -i "1s|\$| drm.edid_firmware=HDMI-A-1:edid/tubeboard.bin|" "$CMD"
else
  echo "WARNING: could not read the screen's EDID - is it plugged in and awake?"
  echo "         run this installer again with the screen on, or a monitor"
  echo "         power cut will leave the board zoomed in."
fi

CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
grep -q '^hdmi_force_hotplug' "$CFG" || {
  printf '\n# tube board\nhdmi_force_hotplug=1\ndisable_overscan=1\n' >> "$CFG"
  NEED_REBOOT=yes
}

# No login prompt drawing over the board, from now and not only after the reboot.
systemctl disable --now getty@tty1.service || true

# Hotspot fallback: if the Pi cannot find a known WiFi it starts "TubeBoard-setup",
# and a phone that joins gets a page to enter the new WiFi name and password.
if [ -z "$SKIP_COMITUP" ] && [ -f /etc/comitup.conf ]; then
  sed -i 's/^#\? *ap_name:.*/ap_name: TubeBoard-setup/' /etc/comitup.conf
  grep -q '^ap_name:' /etc/comitup.conf || echo 'ap_name: TubeBoard-setup' >> /etc/comitup.conf
fi

# ddcutil needs i2c-dev, which is not loaded by default.
modprobe i2c-dev 2>/dev/null || true
grep -qx "i2c-dev" /etc/modules 2>/dev/null || echo "i2c-dev" >> /etc/modules

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
