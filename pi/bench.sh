#!/bin/bash
# The bench tests, on the Pi. Run for an hour at the brightness you want:  bash bench.sh
# Prints the Pi's under-voltage record every minute. Any "under-voltage" line
# means the single lead is not good enough at that brightness.
decode() {
  local v=$((16#${1#0x}))
  local out=""
  (( v & 0x1 ))     && out="$out under-voltage NOW;"
  (( v & 0x10000 )) && out="$out under-voltage happened since boot;"
  (( v & 0x8 ))     && out="$out throttled NOW;"
  (( v & 0x80000 )) && out="$out throttled since boot;"
  [ -z "$out" ] && out=" clean"
  echo "$out"
}
echo "framebuffer: $(cat /sys/class/graphics/fb0/virtual_size 2>/dev/null) $(cat /sys/class/graphics/fb0/bits_per_pixel 2>/dev/null)bpp"
while true; do
  t=$(vcgencmd get_throttled | cut -d= -f2)
  echo "$(date +%H:%M:%S)  temp $(vcgencmd measure_temp | cut -d= -f2)  volts-flag $t $(decode "$t")"
  sleep 60
done
