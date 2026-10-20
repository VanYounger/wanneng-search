#!/usr/bin/env bash
# 生成并安装桌面图标 + 开机自启（路径按当前项目位置自动填写）
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
[ -d "$DESKTOP_DIR" ] || DESKTOP_DIR="$HOME/Desktop"

make_entry() {
  cat <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$1
GenericName=AI 搜索助手
Comment=国内国外资料一站查，用中文给你讲明白
Exec=$DIR/start.sh
Path=$DIR
Icon=$DIR/icon.svg
Terminal=false
Categories=Network;Utility;Office;
StartupNotify=false
EOF
}

mkdir -p "$DESKTOP_DIR" "$HOME/.config/autostart" "$HOME/.local/share/applications"

make_entry "万能搜索" > "$DESKTOP_DIR/万能搜索.desktop"
make_entry "万能搜索" > "$HOME/.local/share/applications/wanneng-sousuo.desktop"
make_entry "万能搜索" > "$HOME/.config/autostart/wanneng-sousuo.desktop"

chmod +x "$DESKTOP_DIR/万能搜索.desktop" "$HOME/.local/share/applications/wanneng-sousuo.desktop"
gio set "$DESKTOP_DIR/万能搜索.desktop" metadata::trusted true 2>/dev/null || true
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "已安装："
echo "  桌面图标  $DESKTOP_DIR/万能搜索.desktop"
echo "  应用菜单  $HOME/.local/share/applications/wanneng-sousuo.desktop"
echo "  开机自启  $HOME/.config/autostart/wanneng-sousuo.desktop"
