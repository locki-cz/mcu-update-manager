#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
MAINSAIL_DIR=${MAINSAIL_DIR:-$HOME/mainsail}
MOONRAKER_DIR=${MOONRAKER_DIR:-$HOME/moonraker}
MOONRAKER_CONF=${MOONRAKER_CONF:-$HOME/printer_data/config/moonraker.conf}
COMPONENT="$MOONRAKER_DIR/moonraker/components/mcu_update_manager.py"
RELEASE_TAG=v0.1.0-beta.2
ASSET=mainsail-mcu-v2.19.0.tar.gz
DOWNLOAD_URL="https://github.com/locki-cz/mcu-update-manager/releases/download/$RELEASE_TAG/$ASSET"
INCLUDE='[include ~/mcu-update-manager/mcu_update_manager.cfg]'

for path in "$MAINSAIL_DIR/index.html" "$MOONRAKER_CONF" "$MOONRAKER_DIR/moonraker/components" "$REPO_DIR/moonraker_component_entry.py"; do
    if [[ ! -e "$path" ]]; then
        printf 'Required path is missing: %s\n' "$path" >&2
        exit 1
    fi
done
if [[ "$REPO_DIR" != "$HOME/mcu-update-manager" ]]; then
    echo 'This beta requires a checkout at ~/mcu-update-manager.' >&2
    exit 1
fi
installed_version=$(tr -d '\r\n' < "$MAINSAIL_DIR/.version" 2>/dev/null || true)
if [[ "$installed_version" != 'v2.19.0' ]]; then
    printf 'This beta UI requires Mainsail v2.19.0; found: %s. Nothing was installed.\n' "${installed_version:-unknown}" >&2
    exit 1
fi
for command in curl tar python3 readlink; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done

tmp_dir=$(mktemp -d)
trap 'rm -rf -- "$tmp_dir"' EXIT
printf 'Downloading Mainsail v2.19.0 build...\n'
curl --fail --location --retry 2 --output "$tmp_dir/$ASSET" "$DOWNLOAD_URL"
tar -tzf "$tmp_dir/$ASSET" > "$tmp_dir/contents.txt"
if ! grep -Eq '^\./?index\.html$' "$tmp_dir/contents.txt" || ! grep -q '/assets/' "$tmp_dir/contents.txt"; then
    echo 'Release archive is missing index.html or assets; nothing was installed.' >&2
    exit 1
fi
if grep -Eq '(^/|(^|/)\.\.(/|$)|(^|/)config\.json$)' "$tmp_dir/contents.txt"; then
    echo 'Release archive contains an unsafe path or config.json; nothing was installed.' >&2
    exit 1
fi

stamp=$(date +%Y%m%d-%H%M%S)
backup_dir="$HOME/mcu-update-manager-backups/$stamp"
mkdir -p "$backup_dir"
tar -C "$(dirname "$MAINSAIL_DIR")" -czf "$backup_dir/mainsail-$stamp.tar.gz" "$(basename "$MAINSAIL_DIR")"
cp -p "$MOONRAKER_CONF" "$backup_dir/moonraker.conf"
if [[ -e "$COMPONENT" || -L "$COMPONENT" ]]; then
    cp -P "$COMPONENT" "$backup_dir/mcu_update_manager.py"
fi
printf 'Backups: %s\n' "$backup_dir"

# Keep the entry point linked to the checkout so backend updates cannot leave a stale copy in Moonraker.
ln -sfn "$REPO_DIR/moonraker_component_entry.py" "$COMPONENT"
if [[ "$(readlink -f "$COMPONENT")" != "$REPO_DIR/moonraker_component_entry.py" ]]; then
    echo 'Moonraker entry does not resolve to this checkout; refusing to continue.' >&2
    exit 1
fi
if ! grep -Fqx "$INCLUDE" "$MOONRAKER_CONF"; then
    printf '\n%s\n' "$INCLUDE" >> "$MOONRAKER_CONF"
fi
tar --no-same-owner -xzf "$tmp_dir/$ASSET" -C "$MAINSAIL_DIR"

sudo systemctl restart moonraker
printf 'Waiting for Moonraker API...\n'
for attempt in {1..20}; do
    if curl --fail --silent --show-error --max-time 30 \
        http://127.0.0.1:7125/machine/mcu_update_manager/status \
        -o "$tmp_dir/status.json" 2>/dev/null; then
        python3 - "$tmp_dir/status.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as status_file:
    data = json.load(status_file)
result = data.get("result", data)
if not isinstance(result, dict) or "devices" not in result:
    raise SystemExit("MCU API returned an unexpected response")
print("MCU Update Manager API OK; detected devices:", len(result["devices"]))
PY
        echo 'Installation complete. Refresh Mainsail with Ctrl+F5.'
        exit 0
    fi
    sleep 2
done
echo 'Moonraker did not expose the MCU API. Check: journalctl -u moonraker -n 80 --no-pager' >&2
exit 1
