"""文件锁与原子写入工具模块。

提供两个核心函数：

- ``locked_read_modify_write``：基于 ``fcntl.flock`` 的加锁 read-modify-write，
  防止多个子代理并行写同一 JSON 文件时丢失数据。
- ``atomic_write``：原子写入文本文件（先写临时文件再 rename），替代直接
  ``path.write_text()``，避免半写状态被其他进程读到。

仅在 POSIX 平台（Linux / macOS）可用，因为 ``fcntl.flock`` 是 Unix 系统调用。
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Callable


def locked_read_modify_write(
    path: Path,
    modify_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> Any:
    """对 JSON 文件执行加锁的 read-modify-write 操作。

    流程：

    1. 在 ``path`` 同目录下创建锁文件 ``<path>.lock``，使用
       ``fcntl.flock(LOCK_EX)`` 获取独占锁。
    2. 读取 ``path`` 当前内容并 JSON 解析；文件不存在或解析失败时返回 ``{}``。
    3. 调用 ``modify_fn(data)`` 得到 ``new_data``。
    4. 原子写入：先写临时文件 ``<path>.tmp``，再 ``os.rename`` 替换原文件
       （二者在同一目录，保证 rename 是原子操作）。
    5. 释放锁（由 ``try/finally`` 保证）。
    6. 返回 ``new_data``。

    参数:
        path: 目标 JSON 文件路径。
        modify_fn: 接收当前 dict、返回修改后 dict 的回调函数。

    返回:
        ``modify_fn`` 返回的 ``new_data``。

    注意:
        - ``modify_fn`` 应保证是幂等且无副作用的纯函数（在持锁期间被调用）。
        - 调用方不需要预先创建 ``path``；不存在时按 ``{}`` 处理。
        - 锁文件本身不会被删除，复用即可。
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    # 锁文件目录可能不存在（例如目标路径在尚未创建的子目录下）
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # 以 "a+" 打开：不存在则创建，存在则不截断
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # ---- read ----
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                data = {}
        else:
            data = {}

        # ---- modify ----
        new_data = modify_fn(data)

        # ---- atomic write ----
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        # 临时文件必须与目标文件在同一目录，os.rename 才是原子操作
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(new_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(path))

        return new_data
    finally:
        # 确保锁一定被释放
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def atomic_write(path: Path, content: str) -> None:
    """原子写入文本文件。

    先写入与 ``path`` 同目录的临时文件 ``<path>.tmp``，再通过
    ``os.replace`` 原子替换原文件，避免其他进程读到半写状态。

    参数:
        path: 目标文件路径。
        content: 要写入的文本内容。

    注意:
        - 临时文件与目标文件在同一目录，保证 rename 原子性。
        - 目标目录不存在时会自动创建。
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(str(tmp_path), str(path))
