use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::time::{Duration, Instant};

use super::process::abort_with_writer;
use anyhow::{anyhow, Context, Result};

pub(crate) fn mapping_line(id: u32) -> String {
    format!("{id} {id} 1\n")
}

fn prctl_dumpable(value: i32) -> Result<()> {
    let result = unsafe { libc::prctl(libc::PR_SET_DUMPABLE, value, 0, 0, 0) };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error()).context("PR_SET_DUMPABLE")
    }
}

struct ResetDumpable;

impl ResetDumpable {
    fn finish(self) -> Result<()> {
        #[cfg(test)]
        if std::env::var_os("VELITES_USERNS_TEST_FAULT").as_deref()
            == Some(std::ffi::OsStr::new("reset"))
        {
            return Err(anyhow!("injected reset failure"));
        }
        prctl_dumpable(0)
    }
}

impl Drop for ResetDumpable {
    fn drop(&mut self) {
        let _ = prctl_dumpable(0);
    }
}

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

pub(crate) fn child_exit(child: i32) -> Result<()> {
    let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
    let result = unsafe {
        libc::waitid(
            libc::P_PID,
            child as u32,
            &mut info,
            libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
        )
    };
    if result < 0 {
        return Err(std::io::Error::last_os_error()).context("waitid");
    }
    if unsafe { info.si_pid() } == child {
        return Err(anyhow!(
            "userns launcher child exited with status {}",
            unsafe { info.si_status() }
        ));
    }
    Ok(())
}

fn wait_socket(socket: i32, deadline: Instant, child: Option<i32>) -> Result<()> {
    loop {
        if let Some(child) = child {
            child_exit(child)?;
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(anyhow!("userns launcher synchronization timed out"));
        }
        let timeout = remaining.min(Duration::from_millis(10));
        let mut descriptor = libc::pollfd {
            fd: socket,
            events: libc::POLLIN,
            revents: 0,
        };
        let result = unsafe { libc::poll(&mut descriptor, 1, timeout.as_millis() as i32) };
        if result < 0 {
            return Err(std::io::Error::last_os_error()).context("poll launcher socket");
        }
        if result > 0 {
            return Ok(());
        }
    }
}

pub(crate) fn send_control(socket: i32, value: u8) -> Result<()> {
    let result = unsafe { libc::send(socket, std::ptr::addr_of!(value).cast(), 1, 0) };
    if result != 1 {
        return Err(std::io::Error::last_os_error()).context("send launcher phase");
    }
    Ok(())
}

pub(crate) fn receive_control(
    socket: i32,
    expected: u8,
    deadline: Instant,
    child: Option<i32>,
) -> Result<()> {
    wait_socket(socket, deadline, child)?;
    let mut value = 0u8;
    let result = unsafe { libc::recv(socket, std::ptr::addr_of_mut!(value).cast(), 1, 0) };
    if result == 0 {
        return Err(anyhow!("launcher closed before phase {expected}"));
    }
    if result != 1 {
        return Err(std::io::Error::last_os_error()).context("receive launcher phase");
    }
    if value != expected {
        return Err(anyhow!(
            "unexpected launcher phase {value}, expected {expected}"
        ));
    }
    Ok(())
}

fn wait_child_deadline(child: i32, deadline: Instant) -> Result<()> {
    loop {
        let mut status = 0;
        let result = unsafe { libc::waitpid(child, &mut status, libc::WNOHANG) };
        if result < 0 {
            return Err(std::io::Error::last_os_error()).context("waitpid");
        }
        if result == child {
            if !libc::WIFEXITED(status) || libc::WEXITSTATUS(status) != 0 {
                return Err(anyhow!("userns launcher child exited with status {status}"));
            }
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(anyhow!("userns launcher child timed out"));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(test)]
pub(crate) fn wait_child(child: i32, parent_socket: i32) -> Result<()> {
    let result = wait_child_deadline(child, Instant::now() + Duration::from_secs(5));
    if result.is_err() {
        super::process::abort(child, parent_socket);
    }
    result
}

#[cfg(test)]
pub(crate) fn fault_child_succeeded(fault: &str) -> bool {
    std::process::Command::new(std::env::current_exe().unwrap())
        .args(["parent_write_prepares", "--ignored"])
        .env("VELITES_USERNS_TEST_FAULT", fault)
        .status()
        .unwrap()
        .success()
}

fn namespace_child(socket: i32, deadline: Instant) -> ! {
    let result = (|| {
        if unsafe { libc::unshare(libc::CLONE_NEWUSER) } != 0 {
            return Err(std::io::Error::last_os_error()).context("unshare user namespace");
        }
        prctl_dumpable(1)?;
        let reset = ResetDumpable;
        send_control(socket, b'R')?;
        receive_control(socket, b'G', deadline, None)?;
        #[cfg(test)]
        if std::env::var_os("VELITES_USERNS_TEST_FAULT").as_deref()
            == Some(std::ffi::OsStr::new("open"))
        {
            return Err(anyhow!("injected open failure"));
        }
        let path = std::ffi::CString::new("/proc/self/ns/user").expect("static path");
        let fd = unsafe { libc::open(path.as_ptr(), libc::O_RDONLY) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error()).context("open user namespace");
        }
        let owned = unsafe { OwnedFd::from_raw_fd(fd) };
        reset.finish()?;
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

pub(crate) fn prepare_user_namespace() -> Result<OwnedFd> {
    let deadline = Instant::now() + Duration::from_secs(5);
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
        namespace_child(child_socket, deadline);
    }
    unsafe { libc::close(child_socket) };
    let mut writer = None;
    let result = (|| {
        receive_control(parent_socket, b'R', deadline, Some(child))?;
        let uid = unsafe { libc::getuid() };
        let gid = unsafe { libc::getgid() };
        super::process::write_parent_map(
            child,
            "uid_map",
            &mapping_line(uid),
            deadline,
            &mut writer,
        )?;
        super::process::write_parent_map(child, "setgroups", "deny\n", deadline, &mut writer)?;
        super::process::write_parent_map(
            child,
            "gid_map",
            &mapping_line(gid),
            deadline,
            &mut writer,
        )?;
        send_control(parent_socket, b'G')?;
        wait_socket(parent_socket, deadline, None)?;
        let fd = unsafe { OwnedFd::from_raw_fd(recv_fd(parent_socket)?) };
        wait_child_deadline(child, deadline)?;
        if unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_GETFD) } < 0 {
            return Err(std::io::Error::last_os_error()).context("validate namespace descriptor");
        }
        Ok(fd)
    })();
    match result {
        Ok(fd) => {
            unsafe { libc::close(parent_socket) };
            Ok(fd)
        }
        Err(error) => {
            abort_with_writer(child, parent_socket, writer);
            Err(error)
        }
    }
}
