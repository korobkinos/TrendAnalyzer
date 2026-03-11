#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if ! command -v dpkg-deb >/dev/null 2>&1; then
  echo "dpkg-deb not found. Install dpkg-dev first."
  exit 1
fi

VERSION="$(.venv/bin/python -c "from trend_analyzer.version import APP_VERSION; print(APP_VERSION)")"
ARCH="${1:-amd64}"

if [[ ! -x "dist/TrendClient" || ! -x "dist/TrendRecorder" ]]; then
  echo "Binaries not found. Run ./build_roles_linux.sh first."
  exit 1
fi

OUT_DIR="dist/deb"
WORK_DIR="build/deb"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$OUT_DIR"

# -------- trend-client --------
PKG_CLIENT="$WORK_DIR/trend-client"
mkdir -p "$PKG_CLIENT/DEBIAN" "$PKG_CLIENT/usr/bin" "$PKG_CLIENT/usr/share/applications"
install -m 0755 "dist/TrendClient" "$PKG_CLIENT/usr/bin/trend-client"
cat > "$PKG_CLIENT/usr/share/applications/trend-client.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Trend Client
Exec=/usr/bin/trend-client
Terminal=false
Categories=Utility;
EOF
cat > "$PKG_CLIENT/DEBIAN/control" <<EOF
Package: trend-client
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: Trend Analyzer Team
Description: Trend Analyzer client UI (viewer/configurator)
EOF
dpkg-deb --build "$PKG_CLIENT" "$OUT_DIR/trend-client_${VERSION}_${ARCH}.deb"

# -------- trend-recorder --------
PKG_REC="$WORK_DIR/trend-recorder"
mkdir -p "$PKG_REC/DEBIAN" "$PKG_REC/usr/bin" "$PKG_REC/lib/systemd/system"
install -m 0755 "dist/TrendRecorder" "$PKG_REC/usr/bin/trend-recorder"
install -m 0644 "packaging/linux/trend-recorder.service" "$PKG_REC/lib/systemd/system/trend-recorder.service"
cat > "$PKG_REC/DEBIAN/control" <<EOF
Package: trend-recorder
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: Trend Analyzer Team
Description: Headless Trend Recorder service for Modbus archiving
EOF
cat > "$PKG_REC/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env bash
set -e
if ! id -u trendrec >/dev/null 2>&1; then
  useradd --system --home /var/lib/trend-recorder --create-home --shell /usr/sbin/nologin trendrec
fi
mkdir -p /var/lib/trend-recorder
chown -R trendrec:trendrec /var/lib/trend-recorder
systemctl daemon-reload || true
exit 0
EOF
chmod 0755 "$PKG_REC/DEBIAN/postinst"
cat > "$PKG_REC/DEBIAN/prerm" <<'EOF'
#!/usr/bin/env bash
set -e
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop trend-recorder.service || true
  systemctl disable trend-recorder.service || true
fi
exit 0
EOF
chmod 0755 "$PKG_REC/DEBIAN/prerm"
cat > "$PKG_REC/DEBIAN/postrm" <<'EOF'
#!/usr/bin/env bash
set -e
systemctl daemon-reload || true
exit 0
EOF
chmod 0755 "$PKG_REC/DEBIAN/postrm"
dpkg-deb --build "$PKG_REC" "$OUT_DIR/trend-recorder_${VERSION}_${ARCH}.deb"

echo "DEB packages created:"
echo " - $OUT_DIR/trend-client_${VERSION}_${ARCH}.deb"
echo " - $OUT_DIR/trend-recorder_${VERSION}_${ARCH}.deb"

