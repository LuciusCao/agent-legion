#![cfg(target_os = "linux")]

use super::namespace::*;
use super::process::{abort, abort_with_writer, write_parent_map};
use std::os::fd::AsRawFd;
use std::time::{Duration, Instant};

fn socketpair() -> [i32; 2] {
    let mut sockets = [0i32; 2];
    assert_eq!(
        unsafe { libc::socketpair(libc::AF_UNIX, libc::SOCK_DGRAM, 0, sockets.as_mut_ptr()) },
        0
    );
    sockets
}

fn reap(child: i32) -> i32 {
    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    status
}

fn assert_reaped(child: i32) {
    let mut status = 0;
    assert_eq!(
        unsafe { libc::waitpid(child, &mut status, libc::WNOHANG) },
        -1
    );
}

#[test]
fn out_of_order_phase_is_rejected() {
    let [parent, child_socket] = socketpair();
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        send_control(child_socket, b'X').unwrap();
        unsafe { libc::_exit(0) }
    }
    unsafe { libc::close(child_socket) };
    let error = receive_control(
        parent,
        b'R',
        Instant::now() + Duration::from_secs(1),
        Some(child),
    )
    .expect_err("wrong phase must fail");
    assert!(error.to_string().contains("unexpected launcher phase"));
    unsafe { libc::close(parent) };
    reap(child);
}

#[test]
fn early_child_exit_is_rejected() {
    let [parent, child_socket] = socketpair();
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        unsafe { libc::close(child_socket) };
        unsafe { libc::_exit(0) }
    }
    unsafe { libc::close(child_socket) };
    let error = receive_control(
        parent,
        b'R',
        Instant::now() + Duration::from_secs(1),
        Some(child),
    )
    .expect_err("early child exit must fail");
    assert!(error.to_string().contains("child exited"));
    abort(child, parent);
    assert_reaped(child);
}

#[test]
fn synchronization_timeout_aborts_child() {
    let [parent, child_socket] = socketpair();
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        std::thread::sleep(Duration::from_secs(30));
        unsafe { libc::_exit(0) }
    }
    unsafe { libc::close(child_socket) };
    let error = receive_control(
        parent,
        b'R',
        Instant::now() + Duration::from_millis(50),
        Some(child),
    )
    .expect_err("missing ready must time out");
    assert!(error.to_string().contains("timed out"));
    abort(child, parent);
    assert_reaped(child);
}

#[test]
fn parent_map_open_failure_aborts_child() {
    let [parent, child_socket] = socketpair();
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        std::thread::sleep(Duration::from_secs(30));
        unsafe { libc::_exit(0) }
    }
    unsafe { libc::close(child_socket) };
    let mut writer = None;
    let error = write_parent_map(
        child,
        "missing",
        "deny\n",
        Instant::now() + Duration::from_secs(1),
        &mut writer,
    )
    .expect_err("bad map path must fail");
    assert!(error.to_string().contains("map writer exited"));
    abort_with_writer(child, parent, writer);
    assert_reaped(child);
}

#[test]
fn parent_map_requires_live_child() {
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        unsafe { libc::_exit(0) }
    }
    std::thread::sleep(Duration::from_millis(20));
    let mut writer = None;
    let error = write_parent_map(
        child,
        "uid_map",
        &mapping_line(1001),
        Instant::now() + Duration::from_secs(1),
        &mut writer,
    )
    .expect_err("exited child cannot receive maps");
    assert!(error.to_string().contains("child exited"));
    reap(child);
}

#[test]
fn send_failure_is_fail_closed() {
    let [parent, child_socket] = socketpair();
    unsafe { libc::close(child_socket) };
    let error = send_control(parent, b'G').expect_err("closed peer must fail");
    assert!(error.to_string().contains("send launcher phase"));
    unsafe { libc::close(parent) };
}

#[ignore = "requires unprivileged user namespaces; run explicitly in the native probe"]
#[test]
fn parent_write_prepares_single_line_maps_and_live_fd() {
    let fd = prepare_user_namespace().expect("prepare parent-written namespace");
    let raw = fd.as_raw_fd();
    let flags = unsafe { libc::fcntl(raw, libc::F_GETFD) };
    assert!(flags >= 0 && flags & libc::FD_CLOEXEC == 0);
    let path = format!("/proc/self/fd/{raw}");
    assert!(std::fs::read_link(path)
        .expect("namespace link")
        .to_string_lossy()
        .starts_with("user:["));
    drop(fd);
    assert_eq!(unsafe { libc::fcntl(raw, libc::F_GETFD) }, -1);
}

#[ignore = "requires unprivileged user namespaces; run explicitly in the native probe"]
#[test]
fn final_phase_faults_are_fail_closed() {
    for fault in ["open", "send", "reset"] {
        assert!(!fault_child_succeeded(fault));
    }
}
