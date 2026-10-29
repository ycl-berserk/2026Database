mod config;
mod paths;
mod postgres;
mod server;
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
    let child = server.spawn("127.0.0.1", &home.join("logs").join("desktop.log"))?;
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
    let paste = MenuItem::with_id(app, "paste", "打开粘贴导入", true, None::<&str>)?;
    let retry = MenuItem::with_id(app, "retry", "重新启动本地服务", true, None::<&str>)?;
    let logs = MenuItem::with_id(app, "logs", "打开日志目录", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &paste, &retry, &logs, &quit])?;

    let mut builder = TrayIconBuilder::with_id("main")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => navigate(app, "/"),
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

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
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
        .invoke_handler(tauri::generate_handler![retry_start, quit_app, desktop_status])
        .setup(|app| {
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
