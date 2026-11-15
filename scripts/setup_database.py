"""Provision the local StudyFlow database without printing credentials.

Run from the project with: .venv/bin/python scripts/setup_database.py
First boot may still have a passwordless root after the interrupted installer.
After provisioning, root is switched to local Unix socket authentication.
"""

import os
from pathlib import Path
import secrets
import subprocess
import sys

from dotenv import dotenv_values, set_key
import pymysql

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
ADMIN_COMMAND = [
    "sudo", "mysql", "--protocol=socket",
    "--socket=/var/run/mysqld/mysqld.sock", "--batch", "--skip-column-names",
]


def check_server(version, port):
    if not version.startswith("8.4.") or int(port) != 3307:
        sys.exit("管理员连接不是预期的 MySQL 8.4 / 3307，停止。")


def main():
    values = dotenv_values(ENV)
    defaults = {
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "3307",
        "DB_NAME": "studyflow",
        "DB_USER": "studyflow_app",
    }
    for key, expected in defaults.items():
        if values.get(key) not in (None, "", expected):
            sys.exit(f"{key} 已配置为其他值；为避免修改其他数据库，本脚本停止。")
    password = values.get("DB_PASSWORD") or (secrets.token_hex(24) + "Aa1!")
    # Keep credentials stable across retries and retain all existing LLM values.
    ENV.touch(mode=0o600, exist_ok=True)
    os.chmod(ENV, 0o600)
    for key, value in {**defaults, "DB_PASSWORD": password}.items():
        set_key(ENV, key, value)

    # Escape with the driver's standard SQL literal encoder; never shell-expand
    # a password or put it in a process argument or printed command.
    escaped = pymysql.converters.escape_string(password)
    schema = (ROOT / "schema.sql").read_text()
    statements = [part.strip() for part in schema.split(";") if part.strip()]
    statements += [
        f"CREATE USER IF NOT EXISTS 'studyflow_app'@'localhost' IDENTIFIED BY '{escaped}'",
        "GRANT SELECT, INSERT ON studyflow.chat_sessions TO 'studyflow_app'@'localhost'",
        "GRANT SELECT, INSERT ON studyflow.chat_messages TO 'studyflow_app'@'localhost'",
    ]
    # MySQL has no INSTALL PLUGIN IF NOT EXISTS. Prepare a no-op if installed.
    secure_root = [
        "SET @install_socket = (SELECT IF(COUNT(*) = 0, "
        "'INSTALL PLUGIN auth_socket SONAME ''auth_socket.so''', 'DO 0') "
        "FROM INFORMATION_SCHEMA.PLUGINS WHERE PLUGIN_NAME = 'auth_socket')",
        "PREPARE install_socket FROM @install_socket",
        "EXECUTE install_socket",
        "DEALLOCATE PREPARE install_socket",
        "ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket",
    ]
    admin = None
    try:
        try:
            admin = pymysql.connect(
                unix_socket="/var/run/mysqld/mysqld.sock", user="root",
                password="", autocommit=True, connect_timeout=5,
            )
        except pymysql.err.OperationalError as exc:
            if exc.args[0] not in (1045, 1698):
                raise

        if admin is not None:
            with admin.cursor() as cursor:
                cursor.execute("SELECT VERSION(), @@port")
                version, port = cursor.fetchone()
                check_server(version, port)
                for statement in statements:
                    cursor.execute(statement)
        else:
            # sudo prompts only on the human's local terminal; never ask for
            # the system password in chat. Do not echo SQL on failure.
            subprocess.run(["sudo", "-v"], check=True)
            server = subprocess.run(
                [*ADMIN_COMMAND, "-e", "SELECT VERSION(), @@port"],
                capture_output=True, text=True,
            )
            if server.returncode:
                sys.exit("无法核对管理员连接的 MySQL 实例，未执行初始化。")
            fields = server.stdout.strip().split("\t")
            if len(fields) != 2:
                sys.exit("管理员连接返回异常，未执行初始化。")
            check_server(*fields)
            sql = ";\n".join(statements) + ";\n"
            result = subprocess.run(
                ADMIN_COMMAND,
                input=sql, text=True, capture_output=True,
            )
            if result.returncode:
                sys.exit("管理员初始化失败（未打印可能包含凭据的 SQL），请检查服务和现有账号。")

        # Do not rotate an existing user's password automatically. Prove that
        # the .env credentials match the account before declaring success.
        with pymysql.connect(
            host=defaults["DB_HOST"], port=3307, database="studyflow",
            user="studyflow_app", password=password, charset="utf8mb4",
            connect_timeout=5,
        ) as app_db, app_db.cursor() as cursor:
            cursor.execute("SELECT VERSION(), @@port, CURRENT_USER()")
            print("应用账号连接成功：", cursor.fetchone())
            cursor.execute("SHOW GRANTS")
            grants = {row[0] for row in cursor.fetchall()}
            account = "`studyflow_app`@`localhost`"
            expected = {
                f"GRANT USAGE ON *.* TO {account}",
                f"GRANT SELECT, INSERT ON `studyflow`.`chat_sessions` TO {account}",
                f"GRANT SELECT, INSERT ON `studyflow`.`chat_messages` TO {account}",
            }
            if grants != expected:
                sys.exit("现有应用账号权限超出预期；未自动撤销权限，请管理员检查 SHOW GRANTS。")
            for grant in sorted(grants):
                print(grant)

        if admin is not None:
            with admin.cursor() as cursor:
                for statement in secure_root:
                    cursor.execute(statement)
        else:
            result = subprocess.run(
                ADMIN_COMMAND,
                input=";\n".join(secure_root) + ";\n", text=True, capture_output=True,
            )
            if result.returncode:
                sys.exit("应用账号已配置，但 root socket 认证设置失败，请继续排查。")
        print("root 已改为本机 socket 认证；后续请用 sudo mysql 管理。")
        print("数据库初始化完成；.env 保留原 LLM 配置并已写入 DB 配置（密码未显示）。")
    except pymysql.MySQLError as exc:
        # Driver errors may contain a SQL fragment, so only print the code.
        sys.exit(f"数据库初始化未完成，MySQL 错误码：{exc.args[0]}（凭据未显示）。")
    finally:
        if admin is not None:
            admin.close()


if __name__ == "__main__":
    main()
