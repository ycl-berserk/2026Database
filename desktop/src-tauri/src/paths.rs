use std::env;
use std::path::{Path, PathBuf};

use tauri::{AppHandle, Manager};

fn looks_like_resources(path: &Path) -> bool {
    path.join("pgsql").join("bin").is_dir() && path.join("sidecar").is_dir()
}

/// Tauri 的 path API 可能返回 `\\?\C:\...`；PostgreSQL 工具无法解析该前缀。
fn strip_verbatim(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    let Some(rest) = text.strip_prefix(r"\\?\") else {
        return path;
    };
    if let Some(unc) = rest.strip_prefix(r"UNC\") {
        return PathBuf::from(format!(r"\\{unc}"));
    }
    PathBuf::from(rest)
}

fn candidates(app: Option<&AppHandle>) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Some(app) = app {
        if let Ok(dir) = app.path().resource_dir() {
            paths.push(dir.clone());
            paths.push(dir.join("resources"));
        }
    }
    if let Ok(dir) = env::current_exe() {
        if let Some(parent) = dir.parent() {
            paths.push(parent.to_path_buf());
            paths.push(parent.join("resources"));
            // macOS 下可执行文件在 Contents/MacOS，资源在 Contents/Resources；
            // 无界面自检（--smoke）没有 AppHandle，需要自己补上这两个候选。
            #[cfg(target_os = "macos")]
            if let Some(contents) = parent.parent() {
                paths.push(contents.join("Resources"));
                paths.push(contents.join("Resources").join("resources"));
            }
        }
    }
    paths.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources"));
    paths
}

/// 安装目录下的只读资源根（包含 pgsql/、sidecar/、sql/）。
pub fn resource_root(app: Option<&AppHandle>) -> Result<PathBuf, String> {
    if let Ok(value) = env::var("LS_RESOURCE_DIR") {
        let path = strip_verbatim(PathBuf::from(value));
        if looks_like_resources(&path) {
            return Ok(path);
        }
        return Err(format!(
            "LS_RESOURCE_DIR 不包含 pgsql/ 与 sidecar/：{}",
            path.display()
        ));
    }
    for path in candidates(app) {
        if looks_like_resources(&path) {
            return Ok(strip_verbatim(path));
        }
    }
    Err("未找到桌面资源目录（pgsql/、sidecar/），请先运行 packaging/build.py".to_string())
}

/// 用户数据目录：优先 LS_HOME，其次 Tauri 的 app_local_data_dir。
pub fn data_home(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(value) = env::var("LS_HOME") {
        return Ok(strip_verbatim(PathBuf::from(value)));
    }
    app.path()
        .app_local_data_dir()
        .map(strip_verbatim)
        .map_err(|err| format!("无法定位用户数据目录：{err}"))
}

pub fn sidecar_exe(resources: &Path) -> PathBuf {
    let name = if cfg!(windows) {
        "ls-server.exe"
    } else {
        "ls-server"
    };
    resources.join("sidecar").join(name)
}

pub fn ensure_home(home: &Path) -> Result<(), String> {
    for sub in ["pgdata", "logs", "backups", "run"] {
        std::fs::create_dir_all(home.join(sub))
            .map_err(|err| format!("无法创建 {}：{err}", home.join(sub).display()))?;
    }
    Ok(())
}

/// PostgreSQL 不接受含中文的路径。必要时在 ASCII 目录下建立 junction 转发：
/// 可执行文件在 ProgramData 下共享，数据目录按用户哈希区分。
#[cfg(windows)]
fn ascii_root() -> Result<PathBuf, String> {
    let mut candidates = Vec::new();
    if let Ok(base) = env::var("ProgramData") {
        candidates.push(PathBuf::from(base).join("LearningSpace"));
    }
    if let Ok(base) = env::var("PUBLIC") {
        candidates.push(PathBuf::from(base).join("LearningSpace"));
    }
    for base in candidates {
        if std::fs::create_dir_all(&base).is_ok() {
            return Ok(base);
        }
    }
    Err("无法找到可写的 ASCII 目录（ProgramData / Public）".to_string())
}

#[cfg(windows)]
fn ascii_link(target: &Path, prefix: &str) -> Result<PathBuf, String> {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let base = ascii_root()?;
    std::fs::create_dir_all(target)
        .map_err(|err| format!("无法创建 {}：{err}", target.display()))?;
    let target = target
        .canonicalize()
        .map_err(|err| format!("无法解析 {}：{err}", target.display()))?;
    let mut hasher = DefaultHasher::new();
    target.hash(&mut hasher);
    let suffix = format!("{:x}", hasher.finish());
    let link = base.join(format!("{prefix}-{}", &suffix[..8]));

    let metadata = std::fs::symlink_metadata(&link);
    match metadata {
        Ok(_) => {
            let existing = link.canonicalize();
            if existing.as_ref().map(|path| path == &target).unwrap_or(false) {
                return Ok(link);
            }
            if existing.is_err() {
                std::fs::remove_dir(&link)
                    .map_err(|err| format!("无法清理失效的 junction {}：{err}", link.display()))?;
            } else {
                return Err(format!(
                    "ASCII junction 已指向其他位置：{}",
                    link.display()
                ));
            }
        }
        Err(_) => {}
    }

    std::fs::create_dir_all(&target)
        .map_err(|err| format!("无法创建 {}：{err}", target.display()))?;
    let status = std::process::Command::new("cmd")
        .args(["/C", "mklink", "/J"])
        .arg(&link)
        .arg(&target)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map_err(|err| format!("创建 junction 失败：{err}"))?;
    if !status.success() {
        return Err(format!("无法创建 ASCII junction：{}", link.display()));
    }
    Ok(link)
}

pub fn ascii_pg_bin(resources: &Path) -> Result<PathBuf, String> {
    let root = resources.join("pgsql");
    if root.to_string_lossy().is_ascii() {
        return Ok(root.join("bin"));
    }
    #[cfg(windows)]
    {
        // 必须转发整个 pgsql 根目录：initdb 通过可执行文件所在的 bin/ 推导
        // prefix，再读取 share/postgres.bki；只链接 bin/ 会让它去找 <link>/share。
        Ok(ascii_link(&root, "pgsql")?.join("bin"))
    }
    #[cfg(not(windows))]
    {
        Ok(root.join("bin"))
    }
}

/// initdb/pg_ctl 使用的数据目录；含非 ASCII 路径时通过 junction 转发。
pub fn ascii_pg_data(home: &Path) -> Result<PathBuf, String> {
    let original = home.join("pgdata");
    if original.to_string_lossy().is_ascii() {
        return Ok(original);
    }
    #[cfg(windows)]
    {
        ascii_link(&original, "pgdata")
    }
    #[cfg(not(windows))]
    {
        Ok(original)
    }
}
