use std::net::Ipv4Addr;

use netdev::interface::types::InterfaceType;
use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
pub struct LanCandidate {
    pub ip: String,
    pub interface: String,
    pub description: Option<String>,
    pub if_type: String,
    pub is_up: bool,
    pub is_default: bool,
    pub is_virtual: bool,
    pub recommended: bool,
    pub subnet: Option<String>,
    pub gateway: Option<String>,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ShareInfo {
    pub enabled: bool,
    pub running: bool,
    pub initialized: bool,
    pub port: u16,
    pub bind_host: Option<String>,
    pub selected_ip: Option<String>,
    pub url: Option<String>,
    pub qr_svg: Option<String>,
    pub candidates: Vec<LanCandidate>,
}

fn private_rank(ip: Ipv4Addr) -> u8 {
    let [a, b, ..] = ip.octets();
    match (a, b) {
        (192, 168) => 0,
        (10, _) => 1,
        (172, 16..=31) => 2,
        _ => 9,
    }
}

fn is_private(ip: Ipv4Addr) -> bool {
    private_rank(ip) < 9
}

fn type_is_virtual(if_type: InterfaceType) -> bool {
    matches!(
        if_type,
        InterfaceType::Loopback
            | InterfaceType::Tunnel
            | InterfaceType::Bridge
            | InterfaceType::PeerToPeerWireless
            | InterfaceType::ProprietaryVirtual
            | InterfaceType::Ppp
            | InterfaceType::Slip
            | InterfaceType::Atm
            | InterfaceType::Wwan
            | InterfaceType::Wwanpp
            | InterfaceType::Wwanpp2
    )
}

fn type_label(if_type: InterfaceType) -> String {
    match if_type {
        InterfaceType::Wireless80211 => "Wi-Fi",
        InterfaceType::Ethernet
        | InterfaceType::Ethernet3Megabit
        | InterfaceType::FastEthernetT
        | InterfaceType::FastEthernetFx
        | InterfaceType::GigabitEthernet => "以太网",
        InterfaceType::Tunnel => "隧道",
        InterfaceType::Bridge => "网桥",
        InterfaceType::PeerToPeerWireless => "Wi-Fi Direct",
        InterfaceType::Loopback => "回环",
        InterfaceType::ProprietaryVirtual => "虚拟网卡",
        InterfaceType::Ppp => "拨号",
        InterfaceType::Wwan | InterfaceType::Wwanpp | InterfaceType::Wwanpp2 => "移动网络",
        _ => "其他",
    }
    .to_string()
}

/// 名称/描述里的常见虚拟适配器关键字；类型判定之外的第二道保险。
fn virtual_hint(name: &str, description: Option<&str>) -> bool {
    let mut text = name.to_lowercase();
    if let Some(description) = description {
        text.push(' ');
        text.push_str(&description.to_lowercase());
    }
    [
        "vmware",
        "virtualbox",
        "vbox",
        "vethernet",
        "hyper-v",
        "wsl",
        "docker",
        "tailscale",
        "zerotier",
        "virbr",
        "utun",
        "wintun",
        " tun",
        "tap",
        "vpn",
        "loopback",
        "wi-fi direct",
        "wifi direct",
        "default switch",
        "bluetooth",
        "npcap",
    ]
    .iter()
    .any(|needle| text.contains(needle))
}

fn candidate_key(candidate: &LanCandidate) -> (u8, u8, String, String) {
    let ip = candidate.ip.parse::<Ipv4Addr>().ok();
    let rank = ip.map(private_rank).unwrap_or(9);
    let tier = if candidate.is_virtual {
        3
    } else if candidate.is_default && candidate.gateway.is_some() {
        0
    } else if ip.map(is_private).unwrap_or(false) {
        1
    } else {
        2
    };
    (
        tier,
        rank,
        candidate.interface.to_lowercase(),
        candidate.ip.clone(),
    )
}

pub fn sort_candidates(candidates: &mut [LanCandidate]) {
    candidates.sort_by_key(candidate_key);
}

pub fn mark_recommended(candidates: &mut [LanCandidate]) {
    for candidate in candidates.iter_mut() {
        candidate.recommended = false;
    }
    let index = candidates
        .iter()
        .position(|candidate| !candidate.is_virtual)
        .or_else(|| if candidates.is_empty() { None } else { Some(0) });
    if let Some(index) = index {
        candidates[index].recommended = true;
    }
}

fn reason(candidate: &LanCandidate) -> Option<String> {
    if candidate.is_virtual {
        return Some("虚拟网卡或 VPN，手机通常无法访问".to_string());
    }
    if candidate.gateway.is_none() {
        return Some("未检测到默认网关，可能与手机不在同一网络".to_string());
    }
    if let Ok(ip) = candidate.ip.parse::<Ipv4Addr>() {
        if !is_private(ip) {
            return Some("不是常见家庭/办公局域网地址".to_string());
        }
    }
    None
}

pub fn lan_candidates() -> Vec<LanCandidate> {
    let mut candidates = Vec::new();
    for iface in netdev::get_interfaces() {
        if !iface.is_up() || iface.is_loopback() {
            continue;
        }
        let name = iface
            .friendly_name
            .clone()
            .filter(|name| !name.trim().is_empty())
            .unwrap_or_else(|| iface.name.clone());
        let description = iface.description.clone();
        let gateway = iface
            .gateway
            .as_ref()
            .and_then(|gateway| gateway.ipv4.first().copied())
            .map(|ip| ip.to_string());
        let is_virtual = type_is_virtual(iface.if_type)
            || virtual_hint(&name, description.as_deref())
            || virtual_hint(&iface.name, description.as_deref());
        for network in &iface.ipv4 {
            let ip = network.addr();
            if ip.is_loopback() || ip.is_link_local() || ip.is_unspecified() {
                continue;
            }
            let mut candidate = LanCandidate {
                ip: ip.to_string(),
                interface: name.clone(),
                description: description.clone(),
                if_type: type_label(iface.if_type),
                is_up: true,
                is_default: iface.default,
                is_virtual,
                recommended: false,
                subnet: Some(network.to_string()),
                gateway: gateway.clone(),
                reason: None,
            };
            candidate.reason = reason(&candidate);
            candidates.push(candidate);
        }
    }
    sort_candidates(&mut candidates);
    mark_recommended(&mut candidates);
    candidates
}

/// 用户显式选择的地址仍然存在时优先使用；否则回退到推荐地址。
pub fn select_candidate(
    candidates: &[LanCandidate],
    selected: Option<&str>,
) -> Option<LanCandidate> {
    if let Some(selected) = selected {
        if let Some(candidate) = candidates.iter().find(|candidate| candidate.ip == selected) {
            return Some(candidate.clone());
        }
    }
    candidates
        .iter()
        .find(|candidate| candidate.recommended)
        .or_else(|| candidates.iter().find(|candidate| !candidate.is_virtual))
        .or_else(|| candidates.first())
        .cloned()
}

pub fn url_for(ip: &str, port: u16) -> Option<String> {
    ip.parse::<Ipv4Addr>().ok().map(|ip| share_url(ip, port))
}

pub fn share_url(ip: Ipv4Addr, port: u16) -> String {
    format!("http://{ip}:{port}/")
}

pub fn qr_svg(text: &str) -> Result<String, String> {
    use qrcode::render::svg;

    let code =
        qrcode::QrCode::new(text.as_bytes()).map_err(|err| format!("无法生成二维码：{err}"))?;
    Ok(code
        .render::<svg::Color>()
        .min_dimensions(220, 220)
        .quiet_zone(true)
        .dark_color(svg::Color("#232b25"))
        .light_color(svg::Color("#fffdf7"))
        .build())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(
        ip: &str,
        interface: &str,
        is_virtual: bool,
        is_default: bool,
        gateway: Option<&str>,
    ) -> LanCandidate {
        LanCandidate {
            ip: ip.to_string(),
            interface: interface.to_string(),
            description: None,
            if_type: if is_virtual { "虚拟网卡" } else { "Wi-Fi" }.to_string(),
            is_up: true,
            is_default,
            is_virtual,
            recommended: false,
            subnet: Some(format!("{ip}/24")),
            gateway: gateway.map(str::to_string),
            reason: None,
        }
    }

    #[test]
    fn url_uses_ipv4_and_port() {
        assert_eq!(
            share_url(Ipv4Addr::new(192, 168, 1, 12), 8765),
            "http://192.168.1.12:8765/"
        );
        assert_eq!(url_for("not-an-ip", 8765), None);
    }

    #[test]
    fn qr_svg_contains_svg_markup() {
        let svg = qr_svg("http://192.168.1.12:8765/").unwrap();
        assert!(svg.contains("<svg"));
        assert!(svg.contains("</svg>"));
    }

    #[test]
    fn private_rank_prefers_lan_ranges() {
        assert!(
            private_rank(Ipv4Addr::new(192, 168, 0, 2)) < private_rank(Ipv4Addr::new(10, 0, 0, 2))
        );
        assert!(
            private_rank(Ipv4Addr::new(10, 0, 0, 2)) < private_rank(Ipv4Addr::new(172, 20, 0, 2))
        );
        assert!(
            private_rank(Ipv4Addr::new(172, 20, 0, 2)) < private_rank(Ipv4Addr::new(100, 64, 0, 2))
        );
    }

    #[test]
    fn default_physical_interface_beats_wifi_direct_and_vpn() {
        let mut candidates = vec![
            candidate("192.168.137.1", "Wi-Fi Direct", true, false, None),
            candidate("10.8.0.2", "Tailscale", true, false, Some("10.8.0.1")),
            candidate("192.168.1.23", "Wi-Fi", false, true, Some("192.168.1.1")),
            candidate("10.0.0.9", "Ethernet", false, false, Some("10.0.0.1")),
        ];
        sort_candidates(&mut candidates);
        mark_recommended(&mut candidates);

        assert_eq!(candidates[0].ip, "192.168.1.23");
        assert!(candidates[0].recommended);
        assert!(!candidates[1].recommended);
        assert!(candidates[2].is_virtual);
    }

    #[test]
    fn explicit_selection_wins_and_stale_selection_falls_back() {
        let mut candidates = vec![
            candidate("192.168.1.23", "Wi-Fi", false, true, Some("192.168.1.1")),
            candidate("192.168.137.1", "Wi-Fi Direct", true, false, None),
        ];
        sort_candidates(&mut candidates);
        mark_recommended(&mut candidates);

        let selected = select_candidate(&candidates, Some("192.168.137.1")).unwrap();
        assert_eq!(selected.ip, "192.168.137.1");

        let fallback = select_candidate(&candidates, Some("192.168.1.99")).unwrap();
        assert_eq!(fallback.ip, "192.168.1.23");
        assert!(fallback.recommended);
    }

    #[test]
    fn virtual_adapters_are_recognized_by_name_or_type() {
        assert!(virtual_hint("Microsoft Wi-Fi Direct Virtual Adapter", None));
        assert!(virtual_hint("vEthernet (Default Switch)", None));
        assert!(virtual_hint("Ethernet 2", Some("TAP-Windows Adapter V9")));
        assert!(!virtual_hint("Wi-Fi", Some("Intel(R) Wi-Fi 6E AX211")));
        assert!(type_is_virtual(InterfaceType::PeerToPeerWireless));
        assert!(type_is_virtual(InterfaceType::Tunnel));
        assert!(!type_is_virtual(InterfaceType::Wireless80211));
    }
}
