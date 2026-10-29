use std::io::Write;
use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use std::{env, fs::OpenOptions, thread};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use url::Url;

const DEFAULT_BASE_URL: &str = "http://127.0.0.1:8000";
const START_TIMEOUT: Duration = Duration::from_secs(45);

struct DesktopState {
    base_url: String,
    server: Mutex<Option<Child>>,
    starting: AtomicBool,
    status: Mutex<String>,
}

fn base_url() -> String {
    env::var("LS_URL")
        .unwrap_or_else(|_| DEFAULT_BASE_URL.to_string())
        .trim_end_matches('/')
        .to_string()
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

fn project_root() -> PathBuf {
    if let Some(path) = env::var_os("LS_PROJECT_ROOT") {
        return PathBuf::from(path);
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn server_port(base: &str) -> u16 {
    Url::parse(base)
        .ok()
        .and_then(|url| url.port_or_known_default())
        .unwrap_or(8000)
}

fn server_up(base: &str) -> bool {
    let Ok(url) = Url::parse(base) else {
        return false;
    };
    let Some(host) = url.host_str() else {
        return false;
    };
    let port = url.port_or_known_default().unwrap_or(80);
    let Ok(addrs) = (host, port).to_socket_addrs() else {
        return false;
    };
    addrs.into_iter().any(|addr| {
        TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok()
    })
}

fn spawn_server(base: &str) -> Result<Child, String> {
    let root = project_root();
    let port = server_port(base);
    let python = env::var_os("LS_PYTHON").map(PathBuf::from).unwrap_or_else(|| {
        let candidate = if cfg!(windows) {
            root.join(".venv").join("Scripts").join("python.exe")
        } else {
            root.join(".venv").join("bin").join("python")
        };
        if candidate.exists() {
            candidate
        } else {
            PathBuf::from("python")
        }
    });

    let mut command = Command::new(&python);
    command
        .arg("-m")
        .arg("uvicorn")
        .arg("app.main:app")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .current_dir(&root);

    let log_path = root.join("data").join("desktop-server.log");
    match OpenOptions::new().create(true).append(true).open(&log_path) {
        Ok(file) => {
            let errors = file
                .try_clone()
                .map_err(|err| format!("无法写服务日志：{err}"))?;
            command.stdout(Stdio::from(file)).stderr(Stdio::from(errors));
        }
        Err(_) => {
            command.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    command
        .spawn()
        .map_err(|err| format!("无法启动本地服务（{}）：{err}", python.display()))
}

fn set_status(app: &AppHandle, text: &str) {
    if let Ok(mut status) = app.state::<DesktopState>().status.lock() {
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
    let state = app.state::<DesktopState>();
    let url = if path.starts_with("http://") || path.starts_with("https://") {
        path.to_string()
    } else {
        format!("{}{}", state.base_url, path)
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

fn ensure_and_open(app: AppHandle) {
    let state = app.state::<DesktopState>();
    if state.starting.swap(true, Ordering::SeqCst) {
        return;
    }

    thread::spawn(move || {
        let state = app.state::<DesktopState>();
        let base = state.base_url.clone();
        let mut already_running = state
            .server
            .lock()
            .map(|mut child| {
                matches!(
                    child.as_mut().map(|process| process.try_wait()),
                    Some(Ok(None))
                )
            })
            .unwrap_or(false);

        if !server_up(&base) && !already_running {
            if !env_bool("LS_AUTOSTART_SERVER", true) {
                set_status(
                    &app,
                    "LS_AUTOSTART_SERVER=0：请先手动启动本地服务（uv run uvicorn app.main:app），再点击重试。",
                );
                state.starting.store(false, Ordering::SeqCst);
                return;
            }
            set_status(&app, "正在启动本地服务…");
            match spawn_server(&base) {
                Ok(child) => {
                    if let Ok(mut slot) = state.server.lock() {
                        *slot = Some(child);
                    }
                    already_running = true;
                }
                Err(message) => {
                    set_status(&app, &message);
                    state.starting.store(false, Ordering::SeqCst);
                    return;
                }
            }
        }

        let deadline = Instant::now() + START_TIMEOUT;
        while Instant::now() < deadline {
            if server_up(&base) {
                let _ = already_running;
                state.starting.store(false, Ordering::SeqCst);
                navigate(&app, "/login");
                return;
            }
            thread::sleep(Duration::from_millis(400));
        }

        set_status(
            &app,
            "本地服务启动超时。请确认 PostgreSQL 已运行（docker compose up -d db）、\
             依赖与演示数据已初始化，然后点击重试；日志见 data/desktop-server.log。",
        );
        state.starting.store(false, Ordering::SeqCst);
    });
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "打开学习空间", true, None::<&str>)?;
    let paste = MenuItem::with_id(app, "paste", "打开粘贴导入", true, None::<&str>)?;
    let retry = MenuItem::with_id(app, "retry", "重新启动本地服务", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &paste, &retry, &quit])?;

    let mut builder = TrayIconBuilder::with_id("main")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => navigate(app, "/"),
            "paste" => navigate(app, "/imports?mode=paste"),
            "retry" => ensure_and_open(app.clone()),
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

fn stop_server(app: &AppHandle) {
    let child = {
        let state = app.state::<DesktopState>();
        state.server.lock().ok().and_then(|mut slot| slot.take())
    };
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
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
    let state = DesktopState {
        base_url: base_url(),
        server: Mutex::new(None),
        starting: AtomicBool::new(false),
        status: Mutex::new(String::new()),
    };

    let app = tauri::Builder::default()
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
        .manage(state)
        .invoke_handler(tauri::generate_handler![retry_start, quit_app, desktop_status])
        .setup(|app| {
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
            stop_server(app);
        }
    });

    let _ = std::io::stdout().flush();
}
