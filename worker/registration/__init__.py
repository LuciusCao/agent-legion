"""Workspace-scoped 注册 token 与注册重试（issue #234）。

token 文件的发现与校验、按全部 token 注册 Host 并处理 401/瞬时错误的
指数退避重试（#35 全局 register token 已退役）。
"""
