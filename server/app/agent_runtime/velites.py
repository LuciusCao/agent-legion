"""velites runtime adapter：包装 workflows/velites_command.build_velites_command。"""

from server.app.agent_runtime.adapter import ExecutionContract, ExecutionKeyRule, RuntimeAdapter
from server.app.agent_runtime.tool_catalog import VELITES_TOOL_CATALOG
from server.app.workflows.velites_command import build_velites_command

# #476：与 `velites tools list --json` 全等的静态镜像（跨二进制契约测试
# 钉住）；validate 是 forced 档，激活联动在 velites 侧实现。
ADAPTER = RuntimeAdapter(
    name="velites",
    binary="velites",
    build_command=build_velites_command,
    execution=ExecutionContract(
        keys={
            "provider": ExecutionKeyRule(True, "平台连接选择器（models.json key）→ --provider"),
            "model": ExecutionKeyRule(True, "模型 id → --model"),
            "thinking": ExecutionKeyRule(False, "思考档位 → --thinking（空 = runtime 决定）"),
        }
    ),
    tool_catalog=VELITES_TOOL_CATALOG,
)
