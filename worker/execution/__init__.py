"""单次 Agent/code 执行的运行期（issue #234）。

lease 心跳、执行状态机（spawn / cancel / 退出收割）、入参准备（二进制解析、
产物下载、manifest 应用）与执行主流程。由 ``worker/executor.py`` 主循环驱动。
"""
