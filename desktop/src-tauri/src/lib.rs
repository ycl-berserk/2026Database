mod config;
mod paths;
mod postgres;
mod server;
mod share;
mod smoke;

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::process::Child;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use std::{env, io};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_opener::OpenerExt;

use config::DesktopConfig;
use postgres::Postgres;

const START_TIMEOUT: Duration = Duration::from_secs(90);

struct DesktopState {
    home: PathBuf,
    resources: PathBuf,
    pg_bin: PathBuf,
    pg_data: PathBuf,
    config: Mutex<DesktopConfig>,
    server: Mutex<Option<Child>>,
    starting: AtomicBool,
    status: Mutex<String>,
    init_error: Option<String>,
}

fn env_bool(name: &str, default: bool) -> bool {
    match env::var(name) {
        Ok(value) => !matches!(
            value.trim().to_lowercase().as_str(),
            "0" | "false" | "off" | "no"
        ),
        Err(_) => default,
    }
}

fn boot_note(home: &std::path::Path, message: &str) {
    let logs = home.join("logs");
    let _ = fs::create_dir_all(&logs);
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs.join("desktop.log"))
    {
        let _ = writeln!(file, "[boot] {message}");
    }
}

fn set_status(app: &AppHandle, text: &str) {
    let state = app.state::<DesktopState>();
    if let Ok(mut status) = state.status.lock() {
        *status = text.to_string();
    }
    if let Some(window) = app.get_webview_window("main") {
        if let Ok(message) = serde_json::to_string(text) {
            let _ = window.eval(format!(
                "window.__setStatus && window.__setStatus({message});"
            ));
        }
    }
}

fn navigate(app: &AppHandle, path: &str) {
    let port = app
        .state::<DesktopState>()
        .config
        .lock()
        .map(|config| config.app_port)
        .unwrap_or(8765);
    let url = if path.starts_with("http://") || path.starts_with("https://") {
        path.to_string()
    } else {
        format!("http://127.0.0.1:{port}{path}")
    };
    if let Some(window) = app.get_webview_window("main") {
        if let Ok(target) = serde_json::to_string(&url) {
            let _ = window.eval(format!("window.location.replace({target});"));
        }
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn toggle_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let visible = window.is_visible().unwrap_or(true);
        if visible {
            let _ = window.hide();
        } else {
            let _ = window.show();
            let _ = window.set_focus();
        }
    }
}

fn stop_child(state: &DesktopState) {
    if let Ok(mut slot) = state.server.lock() {
        if let Some(mut child) = slot.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn make_server(state: &DesktopState, cfg: &DesktopConfig) -> server::Server {
    server::Server::new(
        state.resources.clone(),
        state.home.clone(),
        state.pg_bin.clone(),
        cfg.app_port,
        cfg.pg_port,
    )
}

fn wait_for_health(state: &DesktopState, server: &server::Server) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(45);
    while Instant::now() < deadline {
        if server.health().is_some() {
            return Ok(());
        }
        if let Ok(mut slot) = state.server.lock() {
            if let Some(child) = slot.as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    slot.take();
                    return Err(format!(
                        "本地服务已退出（{status}），请查看日志：{}",
                        state.home.join("logs").display()
                    ));
                }
            }
        }
        thread::sleep(Duration::from_millis(300));
    }
    Err(format!(
        "本地服务启动超时，请查看日志：{}",
        state.home.join("logs").display()
    ))
}

/// 用新的绑定地址重启 sidecar；失败时调用方负责回滚。
fn respawn_server(app: &AppHandle, host: &str) -> Result<(), String> {
    let state = app.state::<DesktopState>();
    let cfg = state
        .config
        .lock()
        .map_err(|_| "配置被占用，请稍后重试".to_string())?
        .clone();

    stop_child(&state);
    config::clear_state(&state.home);

    let server = make_server(&state, &cfg);
    let child = server.spawn(host, &state.home.join("logs").join("desktop.log"))?;
    let pid = child.id();
    let runtime = config::RuntimeState {
        sidecar_pid: pid,
        sidecar_exe: server.exe.to_string_lossy().to_string(),
        pg_port: cfg.pg_port,
    };
    if let Ok(mut slot) = state.server.lock() {
        *slot = Some(child);
    }
    config::write_state(&state.home, &runtime)?;
    boot_note(&state.home, &format!("服务已在 {host} 重启：{pid}"));

    let result = wait_for_health(&state, &server);
    if result.is_err() {
        stop_child(&state);
        config::clear_state(&state.home);
    }
    result
}

fn build_share_info(app: &AppHandle) -> Result<share::ShareInfo, String> {
    let state = app.state::<DesktopState>();
    let cfg = state
        .config
        .lock()
        .map_err(|_| "配置被占用，请稍后重试".to_string())?
        .clone();
    let health = make_server(&state, &cfg).health();
    let running = health.is_some();
    let initialized = health.map(|health| health.initialized).unwrap_or(false);

    let url = share::local_ipv4().map(|ip| share::share_url(ip, cfg.app_port));
    let qr_svg = url.as_deref().and_then(|url| share::qr_svg(url).ok());
    Ok(share::ShareInfo {
        enabled: cfg.lan_enabled,
        running,
        initialized,
        port: cfg.app_port,
        url,
        qr_svg,
    })
}

fn show_share_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("share") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn stop_runtime(app: &AppHandle) {
    let state = app.state::<DesktopState>();
    stop_child(&state);
    config::clear_state(&state.home);

    let port = state
        .config
        .lock()
        .map(|config| config.pg_port)
        .unwrap_or(0);
    if port > 0 {
        let pg = Postgres::new(
            state.pg_bin.clone(),
            state.pg_data.clone(),
            port,
            state.home.join("logs").join("postgresql.log"),
        );
        let _ = pg.stop();
    }
}

fn boot(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<DesktopState>();
    if let Some(message) = &state.init_error {
        return Err(message.clone());
    }

    let home = state.home.clone();
    boot_note(&home, "开始启动");
    paths::ensure_home(&home)?;
    config::cleanup_stale(&home);
    let cfg = config::load_or_create(&home)?;
    if let Ok(mut slot) = state.config.lock() {
        *slot = cfg.clone();
    }
    boot_note(
        &home,
        &format!("配置就绪：app {} / pg {}", cfg.app_port, cfg.pg_port),
    );

    let server = server::Server::new(
        state.resources.clone(),
        home.clone(),
        state.pg_bin.clone(),
        cfg.app_port,
        cfg.pg_port,
    );

    if server.health().is_some() {
        boot_note(&home, "复用已运行的服务");
        navigate(app, "/");
        return Ok(());
    }
    stop_child(&state);

    set_status(app, "准备数据库（首次启动会稍慢）…");
    server.init_env()?;
    boot_note(&home, "本机配置已生成");
    let password = server.pg_password()?;

    let pg = Postgres::new(
        state.pg_bin.clone(),
        state.pg_data.clone(),
        cfg.pg_port,
        home.join("logs").join("postgresql.log"),
    );
    pg.init(&password)?;
    boot_note(&home, "initdb 完成");
    pg.start()?;
    boot_note(&home, "PostgreSQL 已启动");

    set_status(app, "应用数据库迁移…");
    server.bootstrap()?;
    boot_note(&home, "迁移与角色配置完成");

    set_status(app, "启动本地服务…");
    let host = if cfg.lan_enabled { "0.0.0.0" } else { "127.0.0.1" };
    let child = server.spawn(host, &home.join("logs").join("desktop.log"))?;
    boot_note(&home, &format!("服务进程已启动：{}", child.id()));
    let state_file = config::RuntimeState {
        sidecar_pid: child.id(),
        sidecar_exe: server.exe.to_string_lossy().to_string(),
        pg_port: cfg.pg_port,
    };
    if let Ok(mut slot) = state.server.lock() {
        *slot = Some(child);
    }
    config::write_state(&home, &state_file)?;

    let deadline = Instant::now() + START_TIMEOUT;
    while Instant::now() < deadline {
        if server.health().is_some() {
            boot_note(&home, "服务已就绪");
            navigate(app, "/");
            return Ok(());
        }
        if let Ok(mut slot) = state.server.lock() {
            if let Some(child) = slot.as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    slot.take();
                    return Err(format!(
                        "本地服务已退出（{status}），请查看日志：{}",
                        home.join("logs").display()
                    ));
                }
            }
        }
        thread::sleep(Duration::from_millis(300));
    }

    stop_child(&state);
    Err(format!(
        "本地服务启动超时，请查看日志：{}",
        home.join("logs").display()
    ))
}

fn ensure_and_open(app: AppHandle) {
    let state = app.state::<DesktopState>();
    if state.starting.swap(true, Ordering::SeqCst) {
        return;
    }

    thread::spawn(move || {
        if let Err(message) = boot(&app) {
            let home = app.state::<DesktopState>().home.clone();
            boot_note(&home, &format!("启动失败：{message}"));
            set_status(
                &app,
                &format!(
                    "{message}\n可点击“重新启动”重试；数据库与服务日志位于 {}。",
                    home.join("logs").display()
                ),
            );
        }
        app.state::<DesktopState>()
            .starting
            .store(false, Ordering::SeqCst);
    });
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "打开学习空间", true, None::<&str>)?;
    let share = MenuItem::with_id(app, "share", "手机访问…", true, None::<&str>)?;
    let paste = MenuItem::with_id(app, "paste", "打开粘贴导入", true, None::<&str>)?;
    let retry = MenuItem::with_id(app, "retry", "重新启动本地服务", true, None::<&str>)?;
    let logs = MenuItem::with_id(app, "logs", "打开日志目录", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &share, &paste, &retry, &logs, &quit])?;

    let mut builder = TrayIconBuilder::with_id("main")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => navigate(app, "/"),
            "share" => show_share_window(app),
            "paste" => navigate(app, "/imports?mode=paste"),
            "retry" => ensure_and_open(app.clone()),
            "logs" => {
                let logs = app.state::<DesktopState>().home.join("logs");
                let _ = fs::create_dir_all(&logs);
                let _ = app.opener().reveal_item_in_dir(&logs);
            }
            "quit" => app.exit(0),
            _ => {}
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

fn register_shortcuts(app: &AppHandle) {
    let shortcuts = [
        Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space),
        Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyV),
    ];
    for shortcut in shortcuts {
        if let Err(err) = app.global_shortcut().register(shortcut) {
            eprintln!("无法注册全局快捷键：{err}");
        }
    }
}

fn start_clipboard_monitor(app: AppHandle) {
    if !env_bool("LS_CLIPBOARD_MONITOR", true) {
        return;
    }
    thread::spawn(move || {
        let Ok(mut clipboard) = arboard::Clipboard::new() else {
            return;
        };
        let mut last = clipboard.get_text().unwrap_or_default();
        loop {
            thread::sleep(Duration::from_millis(1200));
            let Ok(text) = clipboard.get_text() else {
                continue;
            };
            if text == last {
                continue;
            }
            last = text;
            let chars = last.trim().chars().count();
            if chars < 20 {
                continue;
            }
            if let Some(tray) = app.tray_by_id("main") {
                let _ = tray.set_tooltip(Some(format!(
                    "剪贴板有新内容（{chars} 字）\nCtrl+Shift+V 打开粘贴导入"
                )));
            }
        }
    });
}

#[tauri::command]
fn retry_start(app: AppHandle) {
    set_status(&app, "正在重试…");
    ensure_and_open(app);
}

#[tauri::command]
fn share_info(app: AppHandle) -> Result<share::ShareInfo, String> {
    build_share_info(&app)
}

#[tauri::command]
fn set_lan_share(app: AppHandle, enabled: bool) -> Result<share::ShareInfo, String> {
    let state = app.state::<DesktopState>();
    if state.starting.load(Ordering::SeqCst) {
        return Err("正在启动本地服务，请稍后再试".to_string());
    }
    let cfg = state
        .config
        .lock()
        .map_err(|_| "配置被占用，请稍后重试".to_string())?
        .clone();
    if cfg.lan_enabled == enabled {
        return build_share_info(&app);
    }

    if enabled {
        let health = make_server(&state, &cfg)
            .health()
            .ok_or_else(|| "本地服务未运行，请先在托盘菜单重新启动".to_string())?;
        if !health.ok {
            return Err("本地服务尚未就绪，请稍后重试".to_string());
        }
        if !health.initialized {
            return Err("请先在电脑上完成首次账号创建，再开启手机访问".to_string());
        }
    }

    let (target, fallback) = if enabled {
        ("0.0.0.0", "127.0.0.1")
    } else {
        ("127.0.0.1", "0.0.0.0")
    };
    if let Err(err) = respawn_server(&app, target) {
        let _ = respawn_server(&app, fallback);
        boot_note(&state.home, &format!("切换手机访问失败：{err}"));
        return Err(err);
    }

    {
        let mut slot = state
            .config
            .lock()
            .map_err(|_| "配置被占用，请稍后重试".to_string())?;
        slot.lan_enabled = enabled;
        config::save(&state.home, &slot)?;
    }
    boot_note(
        &state.home,
        if enabled {
            "已开启手机访问（仅应用端口监听 0.0.0.0，PostgreSQL 保持 127.0.0.1）"
        } else {
            "已关闭手机访问（恢复仅监听 127.0.0.1）"
        },
    );
    build_share_info(&app)
}

#[tauri::command]
fn copy_share_url(app: AppHandle) -> Result<String, String> {
    let info = build_share_info(&app)?;
    let url = info
        .url
        .ok_or_else(|| "未检测到局域网地址，请确认电脑已连接 Wi-Fi".to_string())?;
    let mut clipboard =
        arboard::Clipboard::new().map_err(|err| format!("无法访问剪贴板：{err}"))?;
    clipboard
        .set_text(url.clone())
        .map_err(|err| format!("无法写入剪贴板：{err}"))?;
    Ok(url)
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn desktop_status(state: tauri::State<DesktopState>) -> String {
    state
        .status
        .lock()
        .map(|status| status.clone())
        .unwrap_or_default()
}

pub fn run() {
    if env::args().any(|arg| arg == "--smoke") {
        std::process::exit(smoke::run());
    }
    let open_share = env::args().any(|arg| arg == "--share");

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            if argv.iter().any(|arg| arg == "--share") {
                show_share_window(app);
                return;
            }
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    if shortcut.matches(Modifiers::CONTROL | Modifiers::SHIFT, Code::Space) {
                        toggle_window(app);
                    } else if shortcut
                        .matches(Modifiers::CONTROL | Modifiers::SHIFT, Code::KeyV)
                    {
                        navigate(app, "/imports?mode=paste");
                    }
                })
                .build(),
        )
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            retry_start,
            quit_app,
            desktop_status,
            share_info,
            set_lan_share,
            copy_share_url
        ])
        .setup(move |app| {
            let home = paths::data_home(app.handle()).map_err(io::Error::other)?;
            paths::ensure_home(&home).map_err(io::Error::other)?;

            let mut init_error = None;
            let (resources, pg_bin, pg_data) =
                match paths::resource_root(Some(app.handle())) {
                    Ok(resources) => match (
                        paths::ascii_pg_bin(&resources),
                        paths::ascii_pg_data(&home),
                    ) {
                        (Ok(pg_bin), Ok(pg_data)) => (resources, pg_bin, pg_data),
                        (Err(err), _) | (_, Err(err)) => {
                            init_error = Some(err);
                            (PathBuf::new(), PathBuf::new(), PathBuf::new())
                        }
                    },
                    Err(err) => {
                        init_error = Some(err);
                        (PathBuf::new(), PathBuf::new(), PathBuf::new())
                    }
                };

            let config = config::load_or_create(&home).unwrap_or_default();
            app.manage(DesktopState {
                home,
                resources,
                pg_bin,
                pg_data,
                config: Mutex::new(config),
                server: Mutex::new(None),
                starting: AtomicBool::new(false),
                status: Mutex::new(String::new()),
                init_error,
            });

            build_tray(app.handle())?;
            register_shortcuts(app.handle());
            start_clipboard_monitor(app.handle().clone());
            ensure_and_open(app.handle().clone());
            if open_share {
                show_share_window(app.handle());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("启动学习空间桌面壳失败");

    app.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            stop_runtime(app);
        }
    });
}
