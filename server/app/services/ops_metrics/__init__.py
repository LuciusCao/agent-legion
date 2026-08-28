"""Host 运维指标域：``OpsMetricsService`` 门面 + 采样与查询支撑模块。

包根即原平铺的 ``services/ops_metrics.py``（issue #199 归包），
``OpsMetricsService`` / ``Granularity`` 从包根 import 不变；原
``_ops_metrics_*`` 下划线前缀簇去前缀归入本包：

- ``sampling.py`` / ``workspace_sampling.py`` / ``catchup.py``：采样写入
  （全局 + per-Worker 行、per-workspace 行、停机缺口回填）
- ``series.py`` / ``summary.py`` / ``queue.py`` / ``queue_alert.py`` /
  ``runs.py``：读侧查询（窗口序列、面板 summary、队列健康分类）
"""

from __future__ import annotations

from server.app.services.ops_metrics.service import Granularity, OpsMetricsService

__all__ = ["Granularity", "OpsMetricsService"]
