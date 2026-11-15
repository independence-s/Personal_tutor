#!/usr/bin/env bash
# Narrow repair: replace the malformed local configuration, preserving a backup.
set -euo pipefail

config=/etc/mysql/mysql.conf.d/studyflow-local.cnf
if [[ ! -t 0 ]]; then
  printf '请在本地 WSL 交互式终端运行此脚本。\n' >&2
  exit 1
fi
sudo -v
if sudo test -e "$config"; then
  backup="${config}.bak.$(date +%Y%m%d-%H%M%S).$$"
  sudo cp -p -- "$config" "$backup"
  printf '配置已备份：%s\n' "$backup"
fi
printf '[mysqld]\nbind-address=127.0.0.1\nport=3307\nmysqlx=0\n' | sudo tee "$config" >/dev/null
sudo chmod 644 "$config"
if ! sudo systemctl restart mysql; then
  printf '\n重启失败，以下为服务状态及最近的错误（未删除数据库）：\n' >&2
  sudo systemctl status mysql --no-pager -l || true
  sudo grep '\[ERROR\]' /var/log/mysql/error.log | tail -n 15 || true
  exit 1
fi
sudo systemctl is-active mysql
sudo mysql --protocol=socket -e "SELECT VERSION() AS version, @@port AS port; SELECT user, host, plugin FROM mysql.user WHERE user='root';"
printf '\n服务启动及本机管理员连接检查通过。StudyFlow 数据库接入尚待完成。\n'
