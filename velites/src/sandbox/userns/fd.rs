use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::time::Instant;

use anyhow::{anyhow, Context, Result};

pub(crate) fn send_fd(socket: i32, fd: i32) -> Result<()> {
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

pub(crate) fn recv_fd(socket: i32) -> Result<i32> {
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

fn namespace_writer(target: i32, socket: i32) -> ! {
    let result = (|| {
        #[cfg(test)]
        if std::env::var_os("VELITES_USERNS_TEST_FAULT").as_deref()
            == Some(std::ffi::OsStr::new("open"))
        {
            return Err(anyhow!("injected open failure"));
        }
        let path = std::ffi::CString::new(format!("/proc/{target}/ns/user"))?;
        let fd = unsafe { libc::open(path.as_ptr(), libc::O_RDONLY) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error()).context("open target user namespace");
        }
        let owned = unsafe { OwnedFd::from_raw_fd(fd) };
        #[cfg(test)]
        if std::env::var_os("VELITES_USERNS_TEST_FAULT").as_deref()
            == Some(std::ffi::OsStr::new("send"))
        {
            return Err(anyhow!("injected send failure"));
        }
        send_fd(socket, owned.as_raw_fd())
    })();
    match result {
        Ok(()) => unsafe { libc::_exit(0) },
        Err(error) => {
            eprintln!("userns namespace writer failed: {error:#}");
            unsafe { libc::_exit(1) }
        }
    }
}

pub(crate) fn validate_namespace_fd(target: i32, fd: i32) -> Result<()> {
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
    if flags < 0 {
        return Err(std::io::Error::last_os_error()).context("validate namespace descriptor");
    }
    if flags & libc::FD_CLOEXEC != 0 {
        return Err(anyhow!("namespace descriptor must not be close-on-exec"));
    }
    let actual = std::fs::read_link(format!("/proc/self/fd/{fd}"))
        .context("read namespace descriptor link")?;
    let expected = std::fs::read_link(format!("/proc/{target}/ns/user"))
        .context("read target user namespace")?;
    if actual != expected || !actual.to_string_lossy().starts_with("user:[") {
        return Err(anyhow!(
            "namespace descriptor does not match target user namespace"
        ));
    }
    Ok(())
}

pub(crate) fn receive_namespace_fd(
    target: i32,
    deadline: Instant,
    writer: &mut Option<i32>,
) -> Result<OwnedFd> {
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
        return Err(std::io::Error::last_os_error()).context("namespace fd socketpair");
    }
    let (parent_socket, writer_socket) = (sockets[0], sockets[1]);
    let pid = unsafe { libc::fork() };
    if pid < 0 {
        unsafe {
            libc::close(parent_socket);
            libc::close(writer_socket);
        }
        return Err(std::io::Error::last_os_error()).context("fork namespace writer");
    }
    if pid == 0 {
        unsafe { libc::close(parent_socket) };
        namespace_writer(target, writer_socket);
    }
    unsafe { libc::close(writer_socket) };
    *writer = Some(pid);
    let result = (|| {
        super::process::wait_map_writer(target, pid, deadline)?;
        *writer = None;
        super::namespace::wait_socket(parent_socket, deadline, Some(target))?;
        let fd = unsafe { OwnedFd::from_raw_fd(recv_fd(parent_socket)?) };
        validate_namespace_fd(target, fd.as_raw_fd())?;
        Ok(fd)
    })();
    unsafe { libc::close(parent_socket) };
    result
}
