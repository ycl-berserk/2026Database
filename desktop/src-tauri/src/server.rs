use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::time::Duration;

use crate::paths;

const OUTPUT_TAIL: usize = 4000;

pub struct Health {
    pub ok: bool,
    pub initialized: bool,
    pub bind_host: Option<String>,
}

pub struct Server {
    pub exe: PathBuf,
    pub home: PathBuf,
    pub pg_bin: PathBuf,
    pub app_port: u16,
    pub pg_port: u16,
}

fn tail(text: &str) -> String {
    let trimmed = text.trim();
    let count = trimmed.chars().count();
    if count <= OUTPUT_TAIL {
        return trimmed.to_string();
    }
    trimmed.chars().skip(count - OUTPUT_TAIL).collect()
}

fn failure(label: &str, output: &Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr);
    let stdout = String::from_utf8_lossy(&output.stdout);
    format!(
        "{label} 失败（退出码 {}）：{}",
        output.status.code().unwrap_or(-1),
        tail(&format!("{stdout}\n{stderr}"))
    )
}

pub fn read_env_value(env_file: &Path, key: &str) -> Option<String> {
    let text = fs::read_to_string(env_file).ok()?;
    text.lines()
        .filter_map(|line| line.trim().split_once('='))
        .find(|(name, _)| *name == key)
        .map(|(_, value)| value.trim().to_string())
}

impl Server {
    pub fn new(
        resources: PathBuf,
        home: PathBuf,
        pg_bin: PathBuf,
        app_port: u16,
        pg_port: u16,
    ) -> Self {
        Self {
            exe: paths::sidecar_exe(&resources),
            home,
            pg_bin,
            app_port,
            pg_port,
        }
    }

    fn command(&self, subcommand: &str) -> Command {
        let mut command = Command::new(&self.exe);
        command
            .arg(subcommand)
            .arg("--home")
            .arg(&self.home)
            .arg("--port")
            .arg(self.app_port.to_string())
            .arg("--pg-port")
            .arg(self.pg_port.to_string())
            .env("LS_HOME", &self.home)
            .env("LS_PORT", self.app_port.to_string())
            .env("LS_PG_PORT", self.pg_port.to_string())
            .env("LS_PG_BIN", &self.pg_bin)
            .env("APP_ENV", "production");
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        command
    }

    pub fn init_env(&self) -> Result<(), String> {
        let output = self
            .command("init-env")
            .stdin(Stdio::null())
            .output()
            .map_err(|err| format!("无法执行 {}：{err}", self.exe.display()))?;
        if !output.status.success() {
            return Err(failure("准备本机配置", &output));
        }
        Ok(())
    }

    pub fn pg_password(&self) -> Result<String, String> {
        let env_file = self.home.join(".env");
        read_env_value(&env_file, "LS_PG_ADMIN_PASSWORD").ok_or_else(|| {
            format!(
                "未在 {} 找到数据库密码，请删除该文件后重试",
                env_file.display()
            )
        })
    }

    pub fn bootstrap(&self) -> Result<(), String> {
        let output = self
            .command("bootstrap")
            .arg("--pg-bin")
            .arg(&self.pg_bin)
            .stdin(Stdio::null())
            .output()
            .map_err(|err| format!("无法执行 {}：{err}", self.exe.display()))?;
        if !output.status.success() {
            return Err(failure("初始化数据库", &output));
        }
        Ok(())
    }

    pub fn backup(&self) -> Result<(), String> {
        let output = self
            .command("backup")
            .arg("--pg-bin")
            .arg(&self.pg_bin)
            .stdin(Stdio::null())
            .output()
            .map_err(|err| format!("无法执行 {}：{err}", self.exe.display()))?;
        if !output.status.success() {
            return Err(failure("备份数据库", &output));
        }
        Ok(())
    }

    pub fn spawn(&self, host: &str, log: &Path) -> Result<Child, String> {
        if let Some(parent) = log.parent() {
            fs::create_dir_all(parent)
                .map_err(|err| format!("无法创建日志目录 {}：{err}", parent.display()))?;
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(log)
            .map_err(|err| format!("无法写入服务日志 {}：{err}", log.display()))?;
        let errors = file
            .try_clone()
            .map_err(|err| format!("无法写入服务日志 {}：{err}", log.display()))?;

        self.command("serve")
            .arg("--pg-bin")
            .arg(&self.pg_bin)
            .arg("--host")
            .arg(host)
            .stdin(Stdio::null())
            .stdout(Stdio::from(file))
            .stderr(Stdio::from(errors))
            .spawn()
            .map_err(|err| format!("无法启动本地服务（{}）：{err}", self.exe.display()))
    }

    pub fn health(&self) -> Option<Health> {
        self.health_via("127.0.0.1")
    }

    pub fn health_via(&self, host: &str) -> Option<Health> {
        let addr = (host, self.app_port).to_socket_addrs().ok()?.next()?;
        let mut stream = TcpStream::connect_timeout(&addr, Duration::from_millis(800)).ok()?;
        stream.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
        let request = format!(
            "GET /healthz HTTP/1.1\r\nHost: {host}:{}\r\nConnection: close\r\n\r\n",
            self.app_port
        );
        stream.write_all(request.as_bytes()).ok()?;
        let mut response = String::new();
        stream.read_to_string(&mut response).ok()?;
        let (head, body) = response.split_once("\r\n\r\n")?;
        if !head.starts_with("HTTP/1.1 200") && !head.starts_with("HTTP/1.0 200") {
            return None;
        }
        let payload: serde_json::Value = serde_json::from_str(body).ok()?;
        Some(Health {
            ok: payload.get("ok").and_then(|value| value.as_bool())?,
            initialized: payload
                .get("initialized")
                .and_then(|value| value.as_bool())
                .unwrap_or(false),
            bind_host: payload
                .get("bind_host")
                .and_then(|value| value.as_str())
                .map(str::to_string),
        })
    }
}
