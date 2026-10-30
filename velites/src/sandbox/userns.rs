//! Prebuilt user-namespace launcher for non-root, non-setuid bubblewrap.

use std::path::PathBuf;

#[cfg(target_os = "linux")]
use anyhow::Context;
use anyhow::{anyhow, Result};

pub struct CommandSpec {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub helper: bool,
}

impl CommandSpec {
    pub fn direct(program: &str, args: &[String]) -> Self {
        Self {
            program: PathBuf::from(program),
            args: args.to_vec(),
            helper: false,
        }
    }

    pub fn command(self) -> std::process::Command {
        let mut command = std::process::Command::new(self.program);
        command.args(self.args);
        if self.helper {
            command
                .env_clear()
                .envs(crate::tools::bash_env::inherited_env());
        }
        command
    }
}

#[cfg(target_os = "linux")]
fn requires_launcher(euid: u32, bwrap_setuid: bool) -> bool {
    euid != 0 && !bwrap_setuid
}

#[cfg(not(target_os = "linux"))]
#[allow(dead_code)]
fn requires_launcher(_euid: u32, _bwrap_setuid: bool) -> bool {
    false
}

#[cfg(target_os = "linux")]
fn resolve_executable(program: &str, cwd: &std::path::Path) -> Result<PathBuf> {
    use std::os::unix::fs::PermissionsExt;

    let candidates = if program.contains('/') {
        vec![PathBuf::from(program)]
    } else {
        std::env::var_os("PATH")
            .map(|paths| {
                std::env::split_paths(&paths)
                    .map(|root| root.join(program))
                    .collect()
            })
            .unwrap_or_default()
    };
    for candidate in candidates {
        let candidate = if candidate.is_absolute() {
            candidate
        } else {
            cwd.join(candidate)
        };
        if let Ok(metadata) = std::fs::metadata(&candidate) {
            if metadata.is_file() && metadata.permissions().mode() & 0o111 != 0 {
                return Ok(std::fs::canonicalize(candidate)?);
            }
        }
    }
    Err(anyhow!("{program} not found or not executable"))
}

#[cfg(target_os = "linux")]
fn launcher_arguments(bwrap: PathBuf, args: &[String]) -> Vec<String> {
    let mut launcher = vec![
        "sandbox".into(),
        "userns-launcher".into(),
        "--".into(),
        bwrap.display().to_string(),
    ];
    launcher.extend(args.iter().cloned());
    launcher
}

pub fn command_spec(
    program: &str,
    args: &[String],
    sandbox_enabled: bool,
    cwd: &std::path::Path,
) -> std::io::Result<CommandSpec> {
    if !sandbox_enabled {
        return Ok(CommandSpec::direct(program, args));
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (program, cwd);
        Ok(CommandSpec::direct(program, args))
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::PermissionsExt;

        let bwrap = resolve_executable(program, cwd).map_err(std::io::Error::other)?;
        let setuid = std::fs::metadata(&bwrap)?.permissions().mode() & 0o4000 != 0;
        if !requires_launcher(unsafe { libc::geteuid() }, setuid) {
            return Ok(CommandSpec::direct(program, args));
        }
        let current = std::env::current_exe()?;
        let launcher_args = launcher_arguments(bwrap, args);
        Ok(CommandSpec {
            program: current,
            args: launcher_args,
            helper: true,
        })
    }
}

#[cfg(target_os = "linux")]
fn mapping_line(id: u32) -> String {
    format!("{id} {id} 1\n")
}

#[cfg(target_os = "linux")]
fn prctl_dumpable(value: i32) -> Result<()> {
    let result = unsafe { libc::prctl(libc::PR_SET_DUMPABLE, value, 0, 0, 0) };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error()).context("PR_SET_DUMPABLE")
    }
}

#[cfg(target_os = "linux")]
struct ResetDumpable;

#[cfg(target_os = "linux")]
impl ResetDumpable {
    fn finish(self) -> Result<()> {
        prctl_dumpable(0)
    }
}

#[cfg(target_os = "linux")]
impl Drop for ResetDumpable {
    fn drop(&mut self) {
        let _ = prctl_dumpable(0);
    }
}

#[cfg(target_os = "linux")]
fn write_map(path: &str, contents: &str) -> Result<()> {
    use std::io::Write;

    let mut file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .with_context(|| format!("open {path}"))?;
    file.write_all(contents.as_bytes())
        .with_context(|| format!("write {path}"))
}

#[cfg(target_os = "linux")]
fn send_fd(socket: i32, fd: i32) -> Result<()> {
    let mut data = [0u8; 1];
    let mut iov = libc::iovec {
        iov_base: data.as_mut_ptr().cast(),
        iov_len: 1,
    };
    let mut control =
        [0u8; unsafe { libc::CMSG_SPACE(std::mem::size_of::<i32>() as u32) as usize }];
    let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
    message.msg_iov = &mut iov;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen = control.len();
    let header = unsafe { libc::CMSG_FIRSTHDR(&message) };
    if header.is_null() {
        return Err(anyhow!("SCM_RIGHTS header missing"));
    }
    unsafe {
        (*header).cmsg_level = libc::SOL_SOCKET;
        (*header).cmsg_type = libc::SCM_RIGHTS;
        (*header).cmsg_len = libc::CMSG_LEN(std::mem::size_of::<i32>() as u32) as usize;
        std::ptr::copy_nonoverlapping(&fd, libc::CMSG_DATA(header).cast::<i32>(), 1);
    }
    if unsafe { libc::sendmsg(socket, &message, 0) } < 0 {
        return Err(std::io::Error::last_os_error()).context("sendmsg SCM_RIGHTS");
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn recv_fd(socket: i32) -> Result<i32> {
    let mut data = [0u8; 1];
    let mut iov = libc::iovec {
        iov_base: data.as_mut_ptr().cast(),
        iov_len: 1,
    };
    let mut control =
        [0u8; unsafe { libc::CMSG_SPACE(std::mem::size_of::<i32>() as u32) as usize }];
    let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
    message.msg_iov = &mut iov;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen = control.len();
    if unsafe { libc::recvmsg(socket, &mut message, 0) } <= 0 {
        return Err(std::io::Error::last_os_error()).context("recvmsg SCM_RIGHTS");
    }
    if message.msg_flags & libc::MSG_CTRUNC != 0 {
        return Err(anyhow!("SCM_RIGHTS control data truncated"));
    }
    let header = unsafe { libc::CMSG_FIRSTHDR(&message) };
    if header.is_null() {
        return Err(anyhow!("SCM_RIGHTS header missing"));
    }
    unsafe {
        if (*header).cmsg_level != libc::SOL_SOCKET || (*header).cmsg_type != libc::SCM_RIGHTS {
            return Err(anyhow!("unexpected control message"));
        }
        if (*header).cmsg_len < libc::CMSG_LEN(std::mem::size_of::<i32>() as u32) as usize {
            return Err(anyhow!("short file descriptor payload"));
        }
        Ok(std::ptr::read_unaligned(
            libc::CMSG_DATA(header) as *const i32
        ))
    }
}

#[cfg(target_os = "linux")]
fn namespace_child(socket: i32) -> ! {
    use std::os::fd::FromRawFd;

    let result = (|| {
        let uid = unsafe { libc::getuid() };
        let gid = unsafe { libc::getgid() };
        if unsafe { libc::unshare(libc::CLONE_NEWUSER) } != 0 {
            return Err(std::io::Error::last_os_error()).context("unshare user namespace");
        }
        prctl_dumpable(1)?;
        let _reset = ResetDumpable;
        write_map("/proc/self/uid_map", &mapping_line(uid))?;
        write_map("/proc/self/setgroups", "deny\n")?;
        write_map("/proc/self/gid_map", &mapping_line(gid))?;
        _reset.finish()?;
        let path = std::ffi::CString::new("/proc/self/ns/user").expect("static path");
        let fd = unsafe { libc::open(path.as_ptr(), libc::O_RDONLY) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error()).context("open user namespace");
        }
        let owned = unsafe { OwnedFd::from_raw_fd(fd) };
        send_fd(socket, owned.as_raw_fd())?;
        Ok(owned)
    })();
    match result {
        Ok(_) => unsafe { libc::_exit(0) },
        Err(error) => {
            eprintln!("userns launcher child failed: {error:#}");
            unsafe { libc::_exit(1) }
        }
    }
}

#[cfg(target_os = "linux")]
use std::os::fd::{AsRawFd, OwnedFd};

#[cfg(target_os = "linux")]
fn wait_child(child: i32, parent_socket: i32) -> Result<()> {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    loop {
        let mut status = 0;
        let result = unsafe { libc::waitpid(child, &mut status, libc::WNOHANG) };
        if result < 0 {
            unsafe { libc::close(parent_socket) };
            return Err(std::io::Error::last_os_error()).context("waitpid");
        }
        if result == child {
            if !libc::WIFEXITED(status) || libc::WEXITSTATUS(status) != 0 {
                unsafe { libc::close(parent_socket) };
                return Err(anyhow!("userns launcher child exited with status {status}"));
            }
            return Ok(());
        }
        if std::time::Instant::now() >= deadline {
            unsafe {
                libc::kill(child, libc::SIGKILL);
                libc::waitpid(child, &mut status, 0);
                libc::close(parent_socket);
            }
            return Err(anyhow!("userns launcher child timed out"));
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
}

#[cfg(target_os = "linux")]
fn prepare_user_namespace() -> Result<OwnedFd> {
    use std::os::fd::FromRawFd;

    let mut sockets = [0i32; 2];
    if unsafe {
        libc::socketpair(
            libc::AF_UNIX,
            libc::SOCK_DGRAM | libc::SOCK_CLOEXEC,
            0,
            sockets.as_mut_ptr(),
        )
    } != 0
    {
        return Err(std::io::Error::last_os_error()).context("socketpair");
    }
    let (parent_socket, child_socket) = (sockets[0], sockets[1]);
    let child = unsafe { libc::fork() };
    if child < 0 {
        unsafe {
            libc::close(parent_socket);
            libc::close(child_socket);
        }
        return Err(std::io::Error::last_os_error()).context("fork");
    }
    if child == 0 {
        unsafe { libc::close(parent_socket) };
        namespace_child(child_socket);
    }
    unsafe { libc::close(child_socket) };
    wait_child(child, parent_socket)?;
    let received = recv_fd(parent_socket).map(|fd| unsafe { OwnedFd::from_raw_fd(fd) });
    unsafe { libc::close(parent_socket) };
    let fd = received.context("receive namespace descriptor")?;
    if unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_GETFD) } < 0 {
        return Err(std::io::Error::last_os_error()).context("validate namespace descriptor");
    }
    Ok(fd)
}

#[cfg(target_os = "linux")]
fn validate_bwrap_args(args: &[String]) -> Result<()> {
    let options_end = args
        .iter()
        .position(|arg| arg == "--")
        .unwrap_or(args.len());
    if args[..options_end].iter().any(|arg| arg == "--userns") {
        return Err(anyhow!("--userns is launcher-owned"));
    }
    Ok(())
}

#[cfg(target_os = "linux")]
pub fn run_launcher(args: &[String]) -> Result<()> {
    use std::os::unix::process::CommandExt;

    let separator = args
        .iter()
        .position(|arg| arg == "--")
        .ok_or_else(|| anyhow!("missing --"))?;
    let bwrap = args
        .get(separator + 1)
        .ok_or_else(|| anyhow!("missing bwrap path"))?;
    let bwrap_args = &args[separator + 2..];
    validate_bwrap_args(bwrap_args)?;
    let namespace = prepare_user_namespace()?;
    let mut command = std::process::Command::new(bwrap);
    command
        .env_clear()
        .envs(crate::tools::bash_env::inherited_env())
        .arg("--userns")
        .arg(namespace.as_raw_fd().to_string())
        .args(bwrap_args);
    let error = command.exec();
    Err(anyhow!(error)).context("exec bwrap")
}

#[cfg(not(target_os = "linux"))]
pub fn run_launcher(_args: &[String]) -> Result<()> {
    Err(anyhow!("userns launcher is only available on Linux"))
}

#[cfg(test)]
mod tests;
