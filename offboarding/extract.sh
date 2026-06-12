#!/bin/bash
# extract.sh — 导出个人目录到本地
# 用法: ./offboarding/extract.sh <your-name> [output-dir]
#
# 示例: ./offboarding/extract.sh jinlong-huo ~/Desktop/group-export

set -e

MEMBER="${1:?Usage: $0 <member-name> [output-dir]}"
OUTPUT="${2:-$HOME/group-export-$MEMBER}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "📦 导出 members/$MEMBER/ → $OUTPUT"

mkdir -p "$OUTPUT"

# 个人目录
if [ -d "$REPO_ROOT/members/$MEMBER" ]; then
    cp -r "$REPO_ROOT/members/$MEMBER" "$OUTPUT/members-$MEMBER"
    echo "  ✓ members/$MEMBER/"
else
    echo "  ⚠ members/$MEMBER/ 不存在"
fi

# 共享模板（只读参考，不属于你，但带着方便）
mkdir -p "$OUTPUT/_shared-templates"
cp -r "$REPO_ROOT/templates" "$OUTPUT/_shared-templates/templates"
cp -r "$REPO_ROOT/paper-notes/template.md" "$OUTPUT/_shared-templates/paper-notes-template.md"
echo "  ✓ 共享模板（只读参考）"

echo ""
echo "✅ 导出完成。共享模板仅供个人参考，不应公开发布。"
echo ""
echo "📋 别忘了："
echo "  - 提交 knowledge-handover.md"
echo "  - 更新 projects.md"
echo "  - 确认权限已回收（见 exit-checklist.md）"
