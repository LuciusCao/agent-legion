#!/usr/bin/env bash
# 导入示例 workflow（education_video_problems_generation）的 4 个示例 skill：
# 把 examples/skills/* 复制到本机 skill 源目录（默认
# ~/.agents/skills/agent-legion/education-video-problems-generation/<skill>/），
# 逐目录 git init + 初始 commit + 打 tag v1.0.0。
#
# 幂等：目标已是 git 仓库且 tag 存在则跳过该 skill，不覆盖用户后续改动；
# 目标目录存在但不是 git 仓库时同样跳过（打印警告，由用户自行处置）。
# 重复执行安全。
#
# 测试/调试可用 AGENT_LEGION_DEMO_SKILLS_DIR 覆盖目标根目录。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$REPO_ROOT/examples/skills"
TARGET_ROOT="${AGENT_LEGION_DEMO_SKILLS_DIR:-$HOME/.agents/skills/agent-legion/education-video-problems-generation}"
TAG="v1.0.0"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "错误：示例 skill 源目录不存在：$SOURCE_DIR" >&2
    exit 1
fi

imported=0
skipped=0
for skill_dir in "$SOURCE_DIR"/*/; do
    name="$(basename "$skill_dir")"
    target="$TARGET_ROOT/$name"

    if [ -d "$target/.git" ] && git -C "$target" rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
        echo "[跳过] ${name}：已是 git 仓库且 tag $TAG 存在（保留本地改动）"
        skipped=$((skipped + 1))
        continue
    fi

    if [ -e "$target" ] && [ ! -d "$target/.git" ]; then
        echo "[警告] ${name}：$target 已存在但不是 git 仓库，跳过（请手动检查后删除或自行初始化）" >&2
        skipped=$((skipped + 1))
        continue
    fi

    if [ ! -d "$target" ]; then
        mkdir -p "$target"
        cp -R "$skill_dir"/. "$target"/
        echo "[复制] $name -> $target"
    fi

    if [ ! -d "$target/.git" ]; then
        git -C "$target" init -q
        git -C "$target" add -A
        git -C "$target" -c user.name="agent-legion-demo" -c user.email="agent-legion-demo@localhost" \
            commit -q -m "Import demo skill $name"
        echo "[初始化] ${name}：git init + 初始 commit"
    fi

    if ! git -C "$target" rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
        git -C "$target" tag "$TAG"
        echo "[打 tag] ${name}：$TAG"
    fi
    imported=$((imported + 1))
done

echo
echo "完成：导入 ${imported} 个，跳过 ${skipped} 个（目标根目录：${TARGET_ROOT}）。"
echo "下一步："
echo "  1. 重启 backend（首次启动会把示例 skill 源种子进 DB）"
echo "  2. make skills-lock（或 admin 设置 → Skill 源管理 → relock）解析示例 skill 的 commit 锁"
