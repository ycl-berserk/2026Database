use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

const BEGIN: &str = "# BEGIN learning-space";
const END: &str = "# END learning-space";

pub struct Postgres {
    pub bin: PathBuf,
    pub data: PathBuf,
    pub port: u16,
    /// 传给 `pg_ctl -l` 的日志路径，必须是 ASCII 视图。
    pub log: PathBuf,
    /// 报错时展示给用户的日志路径（可能是 `Z:\logs\...` 的原始位置）。
    pub log_hint: PathBuf,
}

fn tool(bin: &Path, name: &str) -> PathBuf {
    let file = if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    };
    bin.join(file)
}

fn run(exe: &Path, args: &[&OsStr], label: &str) -> Result<Output, String> {
    let mut command = Command::new(exe);
    command.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let output = command
        .output()
        .map_err(|err| format!("无法执行 {label}（{}）：{err}", exe.display()))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        return Err(format!(
            "{label} 失败（退出码 {}）：{}{}",
            output.status.code().unwrap_or(-1),
            stdout.trim(),
            stderr.trim()
        ));
    }
    Ok(output)
}

impl Postgres {
    pub fn new(bin: PathBuf, data: PathBuf, port: u16, log: PathBuf) -> Self {
        Self {
            bin,
            data,
            port,
            log_hint: log.clone(),
            log,
        }
    }

    pub fn hint(mut self, path: PathBuf) -> Self {
        self.log_hint = path;
        self
    }

    pub fn initialized(&self) -> bool {
        self.data.join("PG_VERSION").is_file()
    }

    pub fn running(&self) -> bool {
        let status = Command::new(tool(&self.bin, "pg_ctl"))
            .arg("status")
            .arg("-D")
            .arg(&self.data)
            .output();
        matches!(status, Ok(output) if output.status.success())
    }

    pub fn init(&self, password: &str) -> Result<(), String> {
        if self.initialized() {
            return Ok(());
        }
        let run_dir = self
            .data
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."));
        let pwfile = run_dir.join("pg-init.pass");
        fs::write(&pwfile, format!("{password}\n"))
            .map_err(|err| format!("无法写入临时密码文件：{err}"))?;

        let data = self.data.as_os_str();
        let auth = OsStr::new("scram-sha-256");
        let pw_arg = format!("--pwfile={}", pwfile.display());
        let result = run(
            &tool(&self.bin, "initdb"),
            &[
                OsStr::new("-D"),
                data,
                OsStr::new("-U"),
                OsStr::new("postgres"),
                OsStr::new("-A"),
                auth,
                OsStr::new("--encoding=UTF8"),
                OsStr::new("--locale=C"),
                OsStr::new(&pw_arg),
            ],
            "initdb",
        );
        let _ = fs::remove_file(&pwfile);
        result.map(|_| ())?;
        self.write_conf()
    }

    fn write_conf(&self) -> Result<(), String> {
        let path = self.data.join("postgresql.conf");
        let existing = fs::read_to_string(&path)
            .map_err(|err| format!("无法读取 {}：{err}", path.display()))?;
        let mut kept = Vec::new();
        let mut skipping = false;
        for line in existing.lines() {
            if line.trim() == BEGIN {
                skipping = true;
                continue;
            }
            if line.trim() == END {
                skipping = false;
                continue;
            }
            if !skipping {
                kept.push(line.to_string());
            }
        }
        let block = format!(
            "{BEGIN}\n\
             listen_addresses = '127.0.0.1'\n\
             port = {}\n\
             max_connections = 20\n\
             shared_buffers = 128MB\n\
             {END}\n",
            self.port
        );
        let body = kept.join("\n") + "\n" + &block;
        fs::write(&path, body).map_err(|err| format!("无法写入 {}：{err}", path.display()))
    }

    pub fn start(&self) -> Result<(), String> {
        if self.running() {
            return Ok(());
        }
        if !self.initialized() {
            return Err("数据库尚未初始化".to_string());
        }
        self.write_conf()?;
        if let Some(parent) = self.log.parent() {
            fs::create_dir_all(parent)
                .map_err(|err| format!("无法创建日志目录 {}：{err}", parent.display()))?;
        }
        // postgres 会继承 pg_ctl 的 stdout/stderr；用管道会导致读取端永远等不到 EOF。
        // pg_ctl 自身的输出丢弃，服务日志由 -l 写入 postgresql.log。
        let status = Command::new(tool(&self.bin, "pg_ctl"))
            .args([
                OsStr::new("start"),
                OsStr::new("-D"),
                self.data.as_os_str(),
                OsStr::new("-l"),
                self.log.as_os_str(),
                OsStr::new("-w"),
                OsStr::new("-t"),
                OsStr::new("60"),
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|err| format!("无法执行 pg_ctl start：{err}"))?;
        if !status.success() {
            return Err(format!(
                "pg_ctl start 失败（退出码 {}），请查看 {}",
                status.code().unwrap_or(-1),
                self.log_hint.display()
            ));
        }
        Ok(())
    }

    pub fn stop(&self) -> Result<(), String> {
        if !self.running() {
            return Ok(());
        }
        let timeout = OsStr::new("30");
        let fast = run(
            &tool(&self.bin, "pg_ctl"),
            &[
                OsStr::new("stop"),
                OsStr::new("-D"),
                self.data.as_os_str(),
                OsStr::new("-m"),
                OsStr::new("fast"),
                OsStr::new("-w"),
                OsStr::new("-t"),
                timeout,
            ],
            "pg_ctl stop",
        );
        if fast.is_ok() {
            return Ok(());
        }
        run(
            &tool(&self.bin, "pg_ctl"),
            &[
                OsStr::new("stop"),
                OsStr::new("-D"),
                self.data.as_os_str(),
                OsStr::new("-m"),
                OsStr::new("immediate"),
                OsStr::new("-w"),
                OsStr::new("-t"),
                timeout,
            ],
            "pg_ctl stop -m immediate",
        )
        .map(|_| ())
    }
}
