"""Host 侧 agent runtime 目录（issue #75）。

runtime 全集的单一事实来源是 ``catalog.AGENT_RUNTIMES``；每个 runtime 一个
adapter（``adapter.RuntimeAdapter``），命令构建经 catalog 分发，manifest
``execution`` 块解析在 ``execution.resolve_execution``。
"""
