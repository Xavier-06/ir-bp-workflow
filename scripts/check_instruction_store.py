#!/usr/bin/env python3
"""
指令库一致性校验
检查 instruction_store_{ir,ic,bp}/ 下 index.json 与实际 .md 文件是否一致
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORES = {
    "ir": ROOT / "instruction_store_ir",
    "ic": ROOT / "instruction_store_ic",
    "bp": ROOT / "instruction_store_bp",
    "lit": ROOT / "instruction_store_lit",
}

errors: list[str] = []
warnings: list[str] = []


def check_store(name: str, store_dir: Path):
    index_path = store_dir / "index.json"
    if not index_path.exists():
        errors.append(f"[{name}] index.json 不存在")
        return

    # 1. 校验 JSON 可解析
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"[{name}] index.json 解析失败: {e}")
        return

    roles = index.get("roles", [])
    indexed_files = {r["file"] for r in roles if "file" in r}

    # 2. index.json 声明的 .md 文件必须存在
    for r in roles:
        f = r.get("file", "")
        if not f:
            warnings.append(f"[{name}] role '{r.get('key', '?')}' 缺少 file 字段")
            continue
        fp = store_dir / f
        if not fp.exists():
            errors.append(f"[{name}] {f} 在 index.json 声明但文件不存在")

    # 3. 磁盘上的 .md 文件必须在 index.json 里（排除 index.json、README、_ 前缀辅助文件）
    # _common_tool_guide.md 等下划线前缀文件是跨角色共享的工具指南，由 launcher 单独注入，
    # 不作为角色指令注册（v13 起不再报 orphan 警告）。
    on_disk = {
        p.name
        for p in store_dir.glob("*.md")
        if not p.name.startswith(".") and not p.name.startswith("_") and p.name != "index.md"
    }
    orphans = on_disk - indexed_files
    if orphans:
        warnings.append(f"[{name}] 以下 .md 文件不在 index.json 里: {', '.join(sorted(orphans))}")

    # 4. pipeline_bindings 的值必须可解析：在 roles 里，或对应 .md 文件存在
    # （IR launcher 把 bindings 值当文件名直读，两种语义都合法）
    bindings = index.get("pipeline_bindings", {})
    role_keys = {r["key"] for r in roles}
    for pipeline, mapping in bindings.items():
        for step, role_key in mapping.items():
            if role_key in role_keys:
                continue
            if (store_dir / f"{role_key}.md").exists():
                continue
            errors.append(
                f"[{name}] pipeline_bindings.{pipeline}.{step} → '{role_key}' 既不在 roles 列表中，对应 .md 文件也不存在"
            )

    # 5. meta 信息
    meta = index.get("meta", {})
    if not meta:
        warnings.append(f"[{name}] index.json 缺少 meta 字段")


def main():
    for name, store_dir in STORES.items():
        if store_dir.exists():
            check_store(name, store_dir)
        else:
            warnings.append(f"[{name}] 目录不存在: {store_dir}")

    if warnings:
        print("⚠ Warnings:")
        for w in warnings:
            print(f"  {w}")

    if errors:
        print("\n❌ Errors (需修复):")
        for e in errors:
            print(f"  {e}")
        print(f"\n共 {len(errors)} 个错误")
        sys.exit(1)
    else:
        print("✅ 所有指令库一致性检查通过")
        sys.exit(0)


if __name__ == "__main__":
    main()
