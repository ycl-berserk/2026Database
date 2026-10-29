use std::net::Ipv4Addr;

use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
pub struct ShareInfo {
    pub enabled: bool,
    pub running: bool,
    pub initialized: bool,
    pub port: u16,
    pub url: Option<String>,
    pub qr_svg: Option<String>,
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

/// 虚拟机、WSL、Docker 等网卡也会占用私有网段，但手机连不上。
fn is_virtual(name: &str) -> bool {
    let name = name.to_lowercase();
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
        "tun",
        "tap",
        "vpn",
        "loopback",
    ]
    .iter()
    .any(|needle| name.contains(needle))
}

/// 取最像“家用局域网”的 IPv4 地址：跳过虚拟网卡，优先 192.168/16、10/8、172.16/12。
pub fn local_ipv4() -> Option<Ipv4Addr> {
    let networks = sysinfo::Networks::new_with_refreshed_list();
    let mut physical: Vec<Ipv4Addr> = Vec::new();
    let mut fallback: Vec<Ipv4Addr> = Vec::new();
    for (name, data) in networks.iter() {
        for network in data.ip_networks() {
            let std::net::IpAddr::V4(ip) = network.addr else {
                continue;
            };
            if ip.is_loopback() || ip.is_link_local() || ip.is_unspecified() {
                continue;
            }
            let bucket = if is_virtual(name) {
                &mut fallback
            } else {
                &mut physical
            };
            if !bucket.contains(&ip) {
                bucket.push(ip);
            }
        }
    }
    let candidates = if physical.is_empty() { fallback } else { physical };
    candidates
        .into_iter()
        .min_by_key(|ip| private_rank(*ip))
}

pub fn share_url(ip: Ipv4Addr, port: u16) -> String {
    format!("http://{ip}:{port}/")
}

pub fn qr_svg(text: &str) -> Result<String, String> {
    use qrcode::render::svg;

    let code = qrcode::QrCode::new(text.as_bytes())
        .map_err(|err| format!("无法生成二维码：{err}"))?;
    Ok(code
        .render::<svg::Color>()
        .min_dimensions(220, 220)
        .quiet_zone(true)
        .dark_color(svg::Color("#111827"))
        .light_color(svg::Color("#ffffff"))
        .build())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url_uses_ipv4_and_port() {
        assert_eq!(
            share_url(Ipv4Addr::new(192, 168, 1, 12), 8765),
            "http://192.168.1.12:8765/"
        );
    }

    #[test]
    fn qr_svg_contains_svg_markup() {
        let svg = qr_svg("http://192.168.1.12:8765/").unwrap();
        assert!(svg.contains("<svg"));
        assert!(svg.contains("</svg>"));
    }

    #[test]
    fn private_rank_prefers_lan_ranges() {
        assert!(private_rank(Ipv4Addr::new(192, 168, 0, 2)) < private_rank(Ipv4Addr::new(10, 0, 0, 2)));
        assert!(private_rank(Ipv4Addr::new(10, 0, 0, 2)) < private_rank(Ipv4Addr::new(172, 20, 0, 2)));
        assert!(private_rank(Ipv4Addr::new(172, 20, 0, 2)) < private_rank(Ipv4Addr::new(100, 64, 0, 2)));
    }
}
