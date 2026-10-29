use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Child;
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::config;
use crate::paths;
use crate::postgres::Postgres;
use crate::server::Server;

#[derive(Serialize)]
struct Step {
    name: String,
    ok: bool,
    seconds: f64,
    error: Option<String>,
}

#[derive(Serialize)]
struct Report {
    ok: bool,
    home: String,
    seconds: f64,
    error: Option<String>,
    steps: Vec<Step>,
}

fn step<T>(
    steps: &mut Vec<Step>,
    name: &str,
    run: impl FnOnce() -> Result<T, String>,
) -> Result<T, String> {
    let started = Instant::now();
    let result = run();
    steps.push(Step {
        name: name.to_string(),
        ok: result.is_ok(),
        seconds: (started.elapsed().as_micros() as f64) / 1_000_000.0,
        error: result.as_ref().err().cloned(),
    });
    result
}

fn wait_health(server: &Server) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(90);
    while Instant::now() < deadline {
        if let Some(health) = server.health() {
            if health.ok {
                return Ok(());
            }
        }
        thread::sleep(Duration::from_millis(400));
    }
    Err("本地服务未在 90 秒内就绪（/healthz 未返回 200）".to_string())
}

fn execute(home: &Path, steps: &mut Vec<Step>) -> Result<(), String> {
    paths::ensure_home(home)?;
    config::cleanup_stale(home);

    let resources = step(steps, "定位资源", || paths::resource_root(None))?;
    let pg_bin = step(steps, "解析 PostgreSQL 路径", || paths::pg_bin(&resources))?;
    let home_pg = step(steps, "解析数据目录", || paths::home_view(home))?;
    let pg_data = home_pg.join("pgdata");
    let cfg = step(steps, "读取配置", || config::load_or_create(home))?;

    let server = Server::new(
        resources,
        home_pg.clone(),
        pg_bin.clone(),
        cfg.app_port,
        cfg.pg_port,
    );
    step(steps, "生成密钥", || server.init_env())?;
    let password = step(steps, "读取密钥", || server.pg_password())?;

    let pg = Postgres::new(
        pg_bin,
        pg_data,
        cfg.pg_port,
        home_pg.join("logs").join("postgresql.log"),
    )
    .hint(home.join("logs").join("postgresql.log"));
    step(steps, "initdb", || pg.init(&password))?;
    step(steps, "启动 PostgreSQL", || pg.start())?;

    let mut child: Option<Child> = None;
    let outcome = (|| -> Result<(), String> {
        step(steps, "应用迁移", || server.bootstrap())?;
        let spawned = step(steps, "启动服务", || {
            server.spawn("127.0.0.1", &home.join("logs").join("desktop.log"))
        })?;
        child = Some(spawned);
        step(steps, "等待 /healthz", || wait_health(&server))?;
        step(steps, "备份数据库", || server.backup())?;
        Ok(())
    })();

    if let Some(mut process) = child {
        let _ = process.kill();
        let _ = process.wait();
    }
    let stopped = step(steps, "停止 PostgreSQL", || pg.stop());
    match outcome {
        Err(err) => Err(err),
        Ok(()) => stopped,
    }
}

pub fn run() -> i32 {
    let started = Instant::now();
    let home = match env::var_os("LS_HOME") {
        Some(value) => PathBuf::from(value),
        None => env::temp_dir().join(format!("ls-smoke-{}", std::process::id())),
    };

    let mut steps = Vec::new();
    let outcome = execute(&home, &mut steps);
    let report = Report {
        ok: outcome.is_ok(),
        home: home.display().to_string(),
        seconds: (started.elapsed().as_micros() as f64) / 1_000_000.0,
        error: outcome.as_ref().err().cloned(),
        steps,
    };

    let report_path = home.join("smoke-report.json");
    let _ = fs::create_dir_all(&home);
    match serde_json::to_string_pretty(&report) {
        Ok(body) => {
            if let Err(err) = fs::write(&report_path, body + "\n") {
                eprintln!("无法写入 smoke 报告：{err}");
            }
        }
        Err(err) => eprintln!("无法序列化 smoke 报告：{err}"),
    }

    for step in &report.steps {
        println!(
            "[{}] {} ({:.2}s)",
            if step.ok { "ok" } else { "fail" },
            step.name,
            step.seconds
        );
    }
    println!("报告：{}", report_path.display());

    match &report.error {
        Some(err) => {
            eprintln!("smoke 失败：{err}");
            paths::release_drives();
            1
        }
        None => {
            println!("smoke 通过（{:.2}s）", report.seconds);
            paths::release_drives();
            0
        }
    }
}
