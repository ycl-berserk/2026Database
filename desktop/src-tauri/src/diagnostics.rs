use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use serde_json::Value;

use crate::config::DesktopConfig;
use crate::server::{Health, Server};
use crate::share::LanCandidate;

#[derive(Clone, Debug, Serialize)]
pub struct DiagnosticHint {
    pub level: String,
    pub title: String,
    pub detail: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct ShareDiagnostics {
    pub generated_at: u64,
    pub server_running: bool,
    pub bind_host: Option<String>,
    pub selected_ip: Option<String>,
    pub url: Option<String>,
    pub local_probe_ok: bool,
    pub lan_probe_ok: bool,
    pub network_profiles: Vec<Value>,
    pub listening: Vec<Value>,
    pub firewall_profiles: Vec<Value>,
    pub hints: Vec<DiagnosticHint>,
    pub phone_probe_url: Option<String>,
}

fn now_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

fn json_list(raw: Option<String>) -> Vec<Value> {
    let Some(raw) = raw else {
        return Vec::new();
    };
    match serde_json::from_str::<Value>(raw.trim()) {
        Ok(Value::Array(items)) => items,
        Ok(Value::Null) => Vec::new(),
        Ok(item) => vec![item],
        Err(_) => Vec::new(),
    }
}

#[cfg(windows)]
fn powershell(script: &str) -> Option<String> {
    use std::os::windows::process::CommandExt;

    let output = Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ])
        .creation_flags(0x0800_0000)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

#[cfg(not(windows))]
fn powershell(_script: &str) -> Option<String> {
    None
}

fn profile_category(profile: &Value) -> String {
    profile
        .get("NetworkCategory")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn profile_alias(profile: &Value) -> String {
    profile
        .get("InterfaceAlias")
        .and_then(Value::as_str)
        .unwrap_or("<接口名>")
        .to_string()
}

fn firewall_enabled(profile: &Value) -> bool {
    profile
        .get("Enabled")
        .and_then(|value| {
            value
                .as_bool()
                .or_else(|| value.as_i64().map(|number| number != 0))
        })
        .unwrap_or(false)
}

fn firewall_blocks_inbound(profile: &Value) -> bool {
    profile
        .get("DefaultInboundAction")
        .and_then(Value::as_str)
        .map(|action| {
            action.eq_ignore_ascii_case("block") || action == "2" || action.contains("阻止")
        })
        .unwrap_or(false)
}

fn listen_address(item: &Value) -> String {
    item.get("LocalAddress")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn hint(level: &str, title: &str, detail: impl Into<String>) -> DiagnosticHint {
    DiagnosticHint {
        level: level.to_string(),
        title: title.to_string(),
        detail: detail.into(),
    }
}

pub fn collect(
    cfg: &DesktopConfig,
    server: &Server,
    candidates: &[LanCandidate],
    selected: Option<&LanCandidate>,
) -> ShareDiagnostics {
    let local = server.health();
    let local_probe_ok = local.as_ref().map(|health| health.ok).unwrap_or(false);
    let bind_host = local
        .as_ref()
        .and_then(|health: &Health| health.bind_host.clone());
    let selected_ip = selected.map(|candidate| candidate.ip.clone());
    let lan = selected.and_then(|candidate| server.health_via(&candidate.ip));
    let lan_probe_ok = lan.as_ref().map(|health| health.ok).unwrap_or(false);
    let url = selected.and_then(|candidate| crate::share::url_for(&candidate.ip, cfg.app_port));
    let phone_probe_url =
        selected.map(|candidate| format!("http://{}:{}/healthz", candidate.ip, cfg.app_port));

    let (network_profiles, listening, firewall_profiles) = if cfg!(windows) {
        let profiles = powershell(
            "Get-NetConnectionProfile | Select-Object Name,InterfaceAlias,NetworkCategory,IPv4Connectivity | ConvertTo-Json -Compress",
        );
        let listening = powershell(&format!(
            "Get-NetTCPConnection -LocalPort {} -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Compress",
            cfg.app_port
        ));
        let firewall = powershell(
            "Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction | ConvertTo-Json -Compress",
        );
        (
            json_list(profiles),
            json_list(listening),
            json_list(firewall),
        )
    } else {
        (Vec::new(), Vec::new(), Vec::new())
    };

    let mut hints = Vec::new();
    let server_running = local.is_some();
    if !server_running {
        hints.push(hint(
            "error",
            "本地服务未运行",
            "请先在托盘菜单重新启动学习空间，再检查手机访问。",
        ));
    } else {
        if !cfg.lan_enabled {
            hints.push(hint(
                "info",
                "手机访问尚未开启",
                "打开上方开关后，服务会监听 0.0.0.0，仅应用端口对外，PostgreSQL 仍只监听本机。",
            ));
        } else if bind_host.as_deref() != Some("0.0.0.0") {
            hints.push(hint(
                "error",
                "服务没有监听局域网",
                format!(
                    "当前 /healthz 报告的绑定地址是 {}。请关闭再重新打开手机访问开关，若仍失败请查看 desktop.log。",
                    bind_host.as_deref().unwrap_or("未知")
                ),
            ));
        }

        if !local_probe_ok {
            hints.push(hint(
                "error",
                "本机回环探测失败",
                "127.0.0.1 无法访问 /healthz，说明应用服务本身异常；请查看日志并重启应用。",
            ));
        }
        if cfg.lan_enabled && !lan_probe_ok {
            hints.push(hint(
                "warn",
                "局域网地址在本机不可达",
                "服务可能未绑定 0.0.0.0、端口被占用，或 VPN / 防火墙拦截了本机到局域网地址的连接。请重启手机访问开关或暂时断开 VPN。",
            ));
        }

        let public_profile = network_profiles
            .iter()
            .find(|profile| profile_category(profile).eq_ignore_ascii_case("public"));
        if let Some(profile) = public_profile {
            let alias = profile_alias(profile);
            hints.push(hint(
                "warn",
                "当前网络被标记为公用网络",
                format!(
                    "Windows 在公用网络下默认阻止入站。确认该 Wi-Fi 可信后，在管理员 PowerShell 执行：\nSet-NetConnectionProfile -InterfaceAlias \"{alias}\" -NetworkCategory Private"
                ),
            ));
        }

        if firewall_profiles
            .iter()
            .any(|profile| firewall_enabled(profile) && firewall_blocks_inbound(profile))
        {
            hints.push(hint(
                "warn",
                "防火墙默认阻止入站",
                format!(
                    "如需放行应用端口，在管理员 PowerShell 执行（端口以本窗口显示为准）：\nNew-NetFirewallRule -DisplayName \"Learning Space LAN\" -Direction Inbound -Action Allow -Protocol TCP -LocalPort {} -Profile Private",
                    cfg.app_port
                ),
            ));
        }

        if bind_host.as_deref() == Some("0.0.0.0")
            && !listening.is_empty()
            && listening
                .iter()
                .all(|item| listen_address(item).starts_with("127."))
        {
            hints.push(hint(
                "warn",
                "监听地址仍然只有回环",
                "系统报告的监听地址均为 127.x；请关闭并重新开启手机访问，让服务以 0.0.0.0 重启。",
            ));
        }

        if selected
            .map(|candidate| candidate.is_virtual)
            .unwrap_or(false)
        {
            hints.push(hint(
                "warn",
                "当前选择的是虚拟网卡或 VPN",
                "手机通常无法访问虚拟网卡、隧道或 VPN 地址；请改选与手机同一 Wi-Fi/局域网下的物理网卡地址。",
            ));
        }
        if candidates.len() > 1 {
            hints.push(hint(
                "info",
                "检测到多个局域网地址",
                "二维码使用上方的选中地址。请选择与手机同一 Wi-Fi 的物理网卡地址；虚拟网卡和 VPN 通常不可用。",
            ));
        }
        if lan_probe_ok {
            hints.push(hint(
                "info",
                "本机已能通过局域网地址访问",
                "这只证明服务已绑定到该地址，不能证明手机可达：本机访问可能绕过 Windows 防火墙。请用手机打开下方自检地址确认。",
            ));
            hints.push(hint(
                "info",
                "手机仍然打不开？",
                "确认手机与电脑连接同一 Wi-Fi/SSID，关闭手机 VPN 或蜂窝网络优先；校园网、酒店和访客 Wi-Fi 常启用客户端隔离，请改用手机热点验证。",
            ));
        }
        if !cfg!(windows) {
            hints.push(hint(
                "info",
                "macOS 入站连接",
                "若手机无法访问，请在“系统设置 → 网络 / 防火墙”确认没有阻止学习空间，并确认手机与电脑在同一 Wi-Fi。",
            ));
        }
        if hints.is_empty() {
            hints.push(hint(
                "info",
                "未发现明显问题",
                "若手机仍打不开，请优先换手机热点验证是否为路由器/校园网的客户端隔离。",
            ));
        }
    }

    ShareDiagnostics {
        generated_at: now_seconds(),
        server_running,
        bind_host,
        selected_ip,
        url,
        local_probe_ok,
        lan_probe_ok,
        network_profiles,
        listening,
        firewall_profiles,
        hints,
        phone_probe_url,
    }
}
