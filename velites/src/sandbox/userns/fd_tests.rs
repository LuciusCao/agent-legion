#![cfg(target_os = "linux")]

use super::fd::*;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};

fn socketpair() -> [i32; 2] {
    let mut sockets = [0i32; 2];
    assert_eq!(
        unsafe { libc::socketpair(libc::AF_UNIX, libc::SOCK_DGRAM, 0, sockets.as_mut_ptr()) },
        0
    );
    sockets
}

fn open_user_namespace(flags: i32) -> OwnedFd {
    let path = std::ffi::CString::new("/proc/self/ns/user").unwrap();
    let fd = unsafe { libc::open(path.as_ptr(), flags) };
    assert!(fd >= 0);
    unsafe { OwnedFd::from_raw_fd(fd) }
}

#[test]
fn scm_rights_roundtrip_preserves_namespace_fd() {
    let sockets = socketpair();
    let source = open_user_namespace(libc::O_RDONLY);
    send_fd(sockets[0], source.as_raw_fd()).expect("send namespace fd");
    let received = unsafe { OwnedFd::from_raw_fd(recv_fd(sockets[1]).expect("receive fd")) };
    validate_namespace_fd(unsafe { libc::getpid() }, received.as_raw_fd())
        .expect("validate received fd");
    assert_eq!(
        std::fs::read_link(format!("/proc/self/fd/{}", received.as_raw_fd())).unwrap(),
        std::fs::read_link(format!("/proc/self/fd/{}", source.as_raw_fd())).unwrap()
    );
    unsafe {
        libc::close(sockets[0]);
        libc::close(sockets[1]);
    }
}

#[test]
fn cloexec_namespace_fd_is_rejected() {
    let fd = open_user_namespace(libc::O_RDONLY | libc::O_CLOEXEC);
    let error = validate_namespace_fd(unsafe { libc::getpid() }, fd.as_raw_fd())
        .expect_err("close-on-exec fd must fail");
    assert!(error.to_string().contains("close-on-exec"));
}

#[ignore = "requires unprivileged user namespaces; run explicitly in the native probe"]
#[test]
fn prepared_fd_can_setns_after_target_reap() {
    let fd = super::namespace::prepare_user_namespace().expect("prepare namespace fd");
    let validator = unsafe { libc::fork() };
    assert!(validator >= 0);
    if validator == 0 {
        let result = unsafe { libc::setns(fd.as_raw_fd(), libc::CLONE_NEWUSER) };
        unsafe { libc::_exit(i32::from(result != 0)) }
    }
    let mut status = 0;
    assert_eq!(
        unsafe { libc::waitpid(validator, &mut status, 0) },
        validator
    );
    assert!(libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0);
}
