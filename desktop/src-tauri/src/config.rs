use std::fs;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};

const DEFAULT_APP_PORT: u16 = 8765;
const DEFAULT_PG_PORT: u16 = 55432;
const PORT_SCAN: u16 = 50;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DesktopConfig {
    pub app_port: u16,
    pub pg_port: u16,
    #[serde(default)]
    pub lan_enabled: bool,
    #[serde(default)]
    pub selected_lan_ip: Option<String>,
}

impl Default for DesktopConfig {
    fn default() -> Self {
        Self {
            app_port: DEFAULT_APP_PORT,
            pg_port: DEFAULT_PG_PORT,
            lan_enabled: false,
            selected_lan_ip: None,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RuntimeState {
    pub sidecar_pid: u32,
    pub sidecar_exe: String,
    pub pg_port: u16,
}

fn port_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn find_free(start: u16) -> Result<u16, String> {
    (start..start.saturating_add(PORT_SCAN))
        .find(|port| port_free(*port))
        .ok_or_else(|| format!("从 {start} 起没有空闲端口，请关闭占用端口的程序后重试"))
}

fn valid_port(port: u16) -> bool {
    port >= 1024
}

pub fn config_path(home: &Path) -> PathBuf {
    home.join("config.json")
}

pub fn load_or_create(home: &Path) -> Result<DesktopConfig, String> {
    let path = config_path(home);
    if path.exists() {
        let text = fs::read_to_string(&path)
            .map_err(|err| format!("无法读取 {}：{err}", path.display()))?;
        let config: DesktopConfig = serde_json::from_str(&text)
            .map_err(|err| format!("配置文件损坏：{}（{err}）", path.display()))?;
        if !valid_port(config.app_port) || !valid_port(config.pg_port) {
            return Err(format!("配置端口无效：{}", path.display()));
        }
        return Ok(config);
    }

    let config = DesktopConfig {
        app_port: find_free(DEFAULT_APP_PORT)?,
        pg_port: find_free(DEFAULT_PG_PORT)?,
        lan_enabled: false,
        selected_lan_ip: None,
    };
    save(home, &config)?;
    Ok(config)
}

pub fn save(home: &Path, config: &DesktopConfig) -> Result<(), String> {
    let path = config_path(home);
    let body =
        serde_json::to_string_pretty(config).map_err(|err| format!("无法序列化配置：{err}"))?;
    fs::write(&path, body + "\n").map_err(|err| format!("无法写入 {}：{err}", path.display()))
}

fn state_path(home: &Path) -> PathBuf {
    home.join("run").join("state.json")
}

pub fn write_state(home: &Path, state: &RuntimeState) -> Result<(), String> {
    let path = state_path(home);
    let body =
        serde_json::to_string_pretty(state).map_err(|err| format!("无法序列化运行状态：{err}"))?;
    fs::write(&path, body + "\n").map_err(|err| format!("无法写入 {}：{err}", path.display()))
}

pub fn clear_state(home: &Path) {
    let _ = fs::remove_file(state_path(home));
}

fn same_exe(left: &Path, right: &Path) -> bool {
    let left = left.canonicalize().unwrap_or_else(|_| left.to_path_buf());
    let right = right.canonicalize().unwrap_or_else(|_| right.to_path_buf());
    if cfg!(windows) {
        left.to_string_lossy()
            .eq_ignore_ascii_case(&right.to_string_lossy())
    } else {
        left == right
    }
}

/// 只结束上次异常退出留下的、exe 路径匹配的 sidecar；随后删除状态文件。
pub fn cleanup_stale(home: &Path) {
    let path = state_path(home);
    let Ok(text) = fs::read_to_string(&path) else {
        return;
    };
    let Ok(state) = serde_json::from_str::<RuntimeState>(&text) else {
        let _ = fs::remove_file(&path);
        return;
    };

    use sysinfo::{Pid, ProcessesToUpdate, System};
    let mut system = System::new_all();
    system.refresh_processes(ProcessesToUpdate::All, true);
    if let Some(process) = system.process(Pid::from_u32(state.sidecar_pid)) {
        let expected = PathBuf::from(&state.sidecar_exe);
        match process.exe() {
            Some(exe) if same_exe(exe, &expected) => {
                process.kill();
                std::thread::sleep(Duration::from_millis(800));
            }
            _ => {}
        }
    }
    let _ = fs::remove_file(&path);
}
