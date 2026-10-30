use std::time::{Duration, Instant};

use anyhow::{anyhow, Context, Result};

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ProcessState {
    Live,
    Reaped(i32),
    Unknown,
}

pub(crate) fn process_state(child: i32) -> ProcessState {
    loop {
        let mut status = 0;
        let result = unsafe { libc::waitpid(child, &mut status, libc::WNOHANG) };
        if result == 0 {
            return ProcessState::Live;
        }
        if result == child {
            return ProcessState::Reaped(status);
        }
        let error = std::io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::EINTR) {
            return ProcessState::Unknown;
        }
    }
}

pub(crate) fn cleanup(child: i32) {
    if process_state(child) != ProcessState::Live {
        return;
    }
    unsafe {
        libc::kill(child, libc::SIGKILL);
    }
    loop {
        let mut status = 0;
        let result = unsafe { libc::waitpid(child, &mut status, 0) };
        if result == child {
            return;
        }
        let error = std::io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::EINTR) {
            continue;
        }
        return;
    }
}

pub(crate) fn abort(child: i32, socket: i32) {
    unsafe { libc::close(socket) };
    cleanup(child);
}

pub(crate) fn abort_with_writer(child: i32, socket: i32, writer: Option<i32>) {
    if let Some(writer) = writer {
        cleanup(writer);
    }
    abort(child, socket);
}

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

fn map_writer(child: i32, name: &str, contents: &str) {
    let result = write_map(&format!("/proc/{child}/{name}"), contents);
    if let Err(error) = result {
        eprintln!("userns map writer failed: {error:#}");
        unsafe { libc::_exit(1) }
    }
    unsafe { libc::_exit(0) }
}

pub(crate) fn wait_map_writer(target: i32, writer: i32, deadline: Instant) -> Result<()> {
    loop {
        super::namespace::child_exit(target)?;
        match process_state(writer) {
            ProcessState::Live => {}
            ProcessState::Reaped(0) => return Ok(()),
            ProcessState::Reaped(status) => {
                return Err(anyhow!("userns map writer exited with status {status}"));
            }
            ProcessState::Unknown => return Err(anyhow!("cannot determine map writer state")),
        }
        if Instant::now() >= deadline {
            return Err(anyhow!("userns map writer timed out"));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

pub(crate) fn write_parent_map(
    child: i32,
    name: &str,
    contents: &str,
    deadline: Instant,
    writer: &mut Option<i32>,
) -> Result<()> {
    super::namespace::child_exit(child)?;
    let pid = unsafe { libc::fork() };
    if pid < 0 {
        return Err(std::io::Error::last_os_error()).context("fork map writer");
    }
    if pid == 0 {
        map_writer(child, name, contents);
    }
    *writer = Some(pid);
    wait_map_writer(child, pid, deadline)?;
    *writer = None;
    Ok(())
}
