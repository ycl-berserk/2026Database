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

/// PostgreSQL 在 Windows 上用 ANSI 代码页（CP_ACP）转换路径，连 junction 的
/// 目标也会被展开后按 ANSI 重新解释，所以中文路径必须换成真正的 ASCII 盘符。
/// 这里用 `subst` 把非 ASCII 目录映射成盘符，PG 侧只看到 `<盘符>:\...`。
#[cfg(windows)]
mod drive {
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::sync::Mutex;

    /// 本次运行创建的盘符，退出时释放，避免盘符大量堆积。
    static CREATED: Mutex<Vec<char>> = Mutex::new(Vec::new());

    fn hash8(path: &Path) -> String {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};

        let mut hasher = DefaultHasher::new();
        path.hash(&mut hasher);
        format!("{:x}", hasher.finish())[..8].to_string()
    }

    /// 为数据目录写一个标记文件，用于下次启动时确认某个盘符就是本仓库的映射，
    /// 而不是用户自己的盘符或指向别处的映射。
    pub fn write_marker(target: &Path) {
        let marker = target.join("run").join(".ascii-drive");
        let _ = std::fs::create_dir_all(marker.parent().unwrap_or(target));
        let _ = std::fs::write(marker, hash8(target));
    }

    fn marker_matches(target: &Path, root: &Path) -> bool {
        let expected = hash8(target);
        std::fs::read_to_string(root.join("run").join(".ascii-drive"))
            .map(|value| value.trim() == expected)
            .unwrap_or(false)
    }

    /// 资源目录通常是只读的，无法写标记，改用 `bin/postgres.exe` 的大小核对。
    fn exe_matches(target: &Path, root: &Path) -> bool {
        let name = "bin/postgres.exe";
        match (std::fs::metadata(target.join(name)), std::fs::metadata(root.join(name))) {
            (Ok(left), Ok(right)) => left.len() == right.len(),
            _ => false,
        }
    }

    fn run_subst(letter: char, target: Option<&Path>) -> bool {
        let mut command = Command::new("cmd");
        command.args(["/C", "subst"]).arg(format!("{letter}:"));
        match target {
            Some(path) => {
                command.arg(path);
            }
            None => {
                command.arg("/D");
            }
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        command
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }

    fn letters() -> impl Iterator<Item = char> {
        ('D'..='Z').rev()
    }

    fn map(
        target: &Path,
        matches: impl Fn(&Path, &Path) -> bool,
        what: &str,
    ) -> Result<PathBuf, String> {
        std::fs::create_dir_all(target)
            .map_err(|err| format!("无法创建 {}：{err}", target.display()))?;
        let target = super::strip_verbatim(
            target
                .canonicalize()
                .map_err(|err| format!("无法解析 {}：{err}", target.display()))?,
        );

        for letter in letters() {
            let root = PathBuf::from(format!("{letter}:\\"));
            if root.metadata().is_ok() {
                if matches(&target, &root) {
                    // 上次异常退出留下的映射，记下来以便本次退出时释放。
                    if let Ok(mut created) = CREATED.lock() {
                        created.push(letter);
                    }
                    return Ok(root);
                }
                continue;
            }
            if !run_subst(letter, Some(&target)) {
                continue;
            }
            if matches(&target, &root) {
                if let Ok(mut created) = CREATED.lock() {
                    created.push(letter);
                }
                return Ok(root);
            }
            let _ = run_subst(letter, None);
        }
        Err(format!(
            "没有可用的空闲盘符来映射{}（{}），请先断开多余的 subst/网络驱动器后重试",
            what,
            target.display()
        ))
    }

    pub fn home_view(home: &Path) -> Result<PathBuf, String> {
        let target = super::strip_verbatim(
            home.canonicalize()
                .map_err(|err| format!("无法解析 {}：{err}", home.display()))?,
        );
        write_marker(&target);
        map(&target, marker_matches, "数据目录")
    }

    pub fn pgsql_root(pgsql: &Path) -> Result<PathBuf, String> {
        map(pgsql, exe_matches, "程序目录")
    }

    pub fn release() {
        let letters = CREATED.lock().map(|mut list| std::mem::take(&mut *list));
        if let Ok(letters) = letters {
            for letter in letters {
                let _ = run_subst(letter, None);
            }
        }
    }
}

/// 交给 PostgreSQL 的 pgsql/bin 视图：路径含非 ASCII 时通过盘符映射成 ASCII。
pub fn pg_bin(resources: &Path) -> Result<PathBuf, String> {
    let pgsql = resources.join("pgsql");
    if pgsql.to_string_lossy().is_ascii() {
        return Ok(pgsql.join("bin"));
    }
    #[cfg(windows)]
    {
        drive::pgsql_root(&pgsql).map(|root| root.join("bin"))
    }
    #[cfg(not(windows))]
    {
        Ok(pgsql.join("bin"))
    }
}

/// 交给 PostgreSQL 与 sidecar 的数据目录视图：`<盘符>:\` 或原生路径。
pub fn home_view(home: &Path) -> Result<PathBuf, String> {
    if home.to_string_lossy().is_ascii() {
        return Ok(home.to_path_buf());
    }
    #[cfg(windows)]
    {
        drive::home_view(home)
    }
    #[cfg(not(windows))]
    {
        Ok(home.to_path_buf())
    }
}

/// 退出时释放本次 `subst` 映射；PG 与 sidecar 必须先停止。
pub fn release_drives() {
    #[cfg(windows)]
    drive::release();
}
