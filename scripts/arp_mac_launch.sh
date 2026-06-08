#!/usr/bin/env bash
# macOS: when a launcher is opened from Finder (non-interactive), re-run it in Terminal.
# Sourced by SETUP and EXEC — not run directly.

arp_mac_launch_if_needed() {
  local root="$1"
  local script_path="$2"   # absolute path to SETUP or EXEC
  local pause_on_exit="${3:-1}"

  [[ "$(uname -s)" == "Darwin" ]] || return 0
  [[ -t 1 ]] && return 0
  [[ -n "${ARP_IN_TERMINAL:-}" ]] && return 0

  local name
  name="$(basename "$script_path")"
  local tmp
  tmp="$(mktemp "/tmp/arp-${name}.XXXX.command")"

  cat >"$tmp" <<LAUNCHER
#!/bin/bash
export ARP_IN_TERMINAL=1
cd $(printf '%q' "$root") || exit 1
chmod +x SETUP EXEC 2>/dev/null || true
$(printf '%q' "$script_path")
_s=\$?
echo
if [[ \$_s -ne 0 ]]; then
  echo "${name} exited with error \$_s."
fi
if [[ ${pause_on_exit} -eq 1 ]]; then
  read -r -p "Press Enter to close…" _
fi
exit \$_s
LAUNCHER

  chmod +x "$tmp"
  open -a Terminal "$tmp"
  exit 0
}
