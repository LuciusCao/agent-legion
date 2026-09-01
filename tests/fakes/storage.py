"""In-memory ``ObjectStorage`` test double（收敛 issue #283 的 14 份 FakeStorage 拷贝）。

满足 ``server/app/storage/s3_client.py`` 的 ``ObjectStorage`` 协议**全部**方法
（协议加方法时这里漏实现会直接显式暴露，而不是运行时 AttributeError）。
不碰网络。差异点经构造参数覆盖，避免各测试文件再各养一份拷贝：

* ``objects``            — put/copy 写入的对象字节（key → bytes）。
* ``deleted``            — delete_object 删除过的 key 列表（record_deletions）。
* ``presigned``          — presign_put 的 (key, expires) 记录。
* ``put_expiries`` / ``get_expiries`` — presign TTL 断言（claim 注入测试）。
* ``presigned_gets``     — presign_get 的 key 记录（claim block 断言）。
* ``opened``             — open_stream 调用计数（缓存命中断言）。
* ``put_calls``          — put_object/put_stream 计数（“不做镜像重传”断言）。
* ``fail_deletes``       — delete_object 抛 botocore ClientError（#204 故障族）。
* ``fail_put``/``fail_put_with`` — put_object 抛 ConnectionError / 指定异常。
"""

from __future__ import annotations

import io
from typing import BinaryIO

from server.app.storage import ObjectHead

# 与协议默认一致的 presign 过期秒数（不 import 私有常量，测试语义自持）。
DEFAULT_PRESIGN_EXPIRY_SECONDS = 3600


class FakeObjectStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(
        self,
        *,
        objects: dict[str, bytes] | None = None,
        record_deletions: bool = True,
        fail_put: bool = False,
        fail_put_with: Exception | None = None,
    ) -> None:
        self.objects: dict[str, bytes] = dict(objects) if objects else {}
        self.record_deletions = record_deletions
        self.fail_put = fail_put
        self.fail_put_with = fail_put_with
        # 可变行为开关：True 时 delete_object 抛 botocore ClientError。
        self.fail_deletes = False
        self.deleted: list[str] = []
        self.presigned: list[tuple[str, int]] = []
        self.put_expiries: list[int] = []
        self.get_expiries: list[int] = []
        self.presigned_gets: list[str] = []
        self.opened = 0
        self.put_calls = 0

    # ---- presign ----------------------------------------------------------

    def presign_put(
        self,
        storage_key: str,
        size_bytes: int,
        expires_seconds: int = DEFAULT_PRESIGN_EXPIRY_SECONDS,
    ) -> str:
        self.presigned.append((storage_key, expires_seconds))
        self.put_expiries.append(expires_seconds)
        return f"https://s3.test/upload/{storage_key}"

    def presign_get(
        self,
        storage_key: str,
        expires_seconds: int = DEFAULT_PRESIGN_EXPIRY_SECONDS,
    ) -> str:
        self.presigned_gets.append(storage_key)
        self.get_expiries.append(expires_seconds)
        return f"https://s3.test/download/{storage_key}"

    # ---- 元数据与读 -------------------------------------------------------

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        return None if payload is None else ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        self.opened += 1
        return io.BytesIO(self.objects[storage_key])

    def open_range(self, storage_key: str, start: int, end: int) -> io.BytesIO:
        # S3 Range 语义：闭区间 [start, end]；调用方已按对象 size 裁剪。
        self.opened += 1
        return io.BytesIO(self.objects[storage_key][start : end + 1])

    # ---- 写 ---------------------------------------------------------------

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        if self.fail_put:
            raise ConnectionError("endpoint unreachable")
        if self.fail_put_with is not None:
            raise self.fail_put_with
        self.put_calls += 1
        self.objects[storage_key] = data

    def put_stream(
        self,
        storage_key: str,
        stream: BinaryIO,
        size_bytes: int,
        content_type: str = "",
    ) -> None:
        self.put_calls += 1
        self.objects[storage_key] = stream.read()

    def delete_object(self, storage_key: str) -> None:
        if self.fail_deletes:
            # #204 窄化后的真实故障族：botocore ClientError（生产 S3 客户端
            # 删除失败的类型）——sweep 只降级这一族，编程错误应上抛。
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "InternalError"}}, "DeleteObject")
        if self.record_deletions:
            self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)

    def copy_object(self, source_key: str, destination_key: str) -> None:
        self.objects[destination_key] = self.objects[source_key]
