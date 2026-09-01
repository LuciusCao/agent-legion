"""Shared test doubles（跨子系统复用的 fake 实现）。

tests/ 根的文件数被基线锁定（config/architecture/test-root-files-baseline.json
只锁根目录），共享 double 放这里；按被替身的子系统分文件（storage.py 对应
server.app.storage 的 ObjectStorage 协议）。
"""
