import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import { Button } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getSkillDetail } from '../../api/executorApi'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import type { SkillFile } from '../../types/executorTypes'
import styles from './WorkflowSkillPreviewPanel.module.css'

/** 详情 panel 内的技能文件预览（原位替换 inspector，不开 dialog）：
 * 左侧文件列表 + 右侧内容，顶部显示 lock 的当前版本（ref · commit 短 sha）。
 * Studio 对话 turn_end 会按 'studioSkillDetail' 前缀失效本查询（useStudioChat），
 * agent 修改技能文件后 panel 自动刷新。 */
export function WorkflowSkillPreviewPanel(props: {
  skillKey: string
  onBack: () => void
}) {
  // TODO(skill-version): 版本选择的数据源缺口——GET /api/skills/tags 只接受
  // 宿主机绝对路径，前端无法从 skill key 推导；待后端预览响应携带 tags（或
  // 提供按 key 查 tags 的端点）后在此加版本下拉，选中 tag 置 ref 状态，经
  // getSkillDetail 第二参数走 ?ref= 重新拉取（wrapper 与 query key 已预留
  // ref 通道）。当前固定展示 lock 的当前版本。
  const ref: string | null = null
  const [selectedPath, setSelectedPath] = useState('SKILL.md')
  const query = useQuery({
    queryKey: extraQueryKeys.studioSkillDetail(props.skillKey, ref),
    queryFn: () => getSkillDetail(props.skillKey, ref ?? undefined),
    enabled: Boolean(props.skillKey),
  })
  const detail = query.data ?? null
  const files = detail?.files ?? []
  const selected = files.find((file) => file.path === selectedPath) ?? files[0]
  const version = detail
    ? `${detail.ref || '未知版本'} · ${detail.commit.slice(0, 7)}`
    : ''
  const error =
    query.error instanceof Error
      ? query.error.message
      : query.isError
        ? '加载失败'
        : ''
  return (
    <section aria-label="技能文件预览" className={styles.panel}>
      <div className={styles.header}>
        <Button
          size="small"
          startIcon={<ArrowBackIcon />}
          onClick={props.onBack}
        >
          返回节点详情
        </Button>
        <span className={styles.title}>{props.skillKey || '未绑定技能'}</span>
        {version && <span className={styles.version}>{version}</span>}
      </div>
      <div className={styles.content}>
        <SkillFileList
          files={files}
          selected={selected}
          onSelect={setSelectedPath}
        />
        <div className={styles.preview}>
          {error && <div className={styles.state}>{error}</div>}
          {!error && !detail && (
            <div className={styles.state}>正在加载技能文件...</div>
          )}
          {detail && files.length === 0 && (
            <div className={styles.state}>本地技能目录不可用</div>
          )}
          {selected && <pre className={styles.code}>{selected.content}</pre>}
        </div>
      </div>
    </section>
  )
}

function SkillFileList(props: {
  files: SkillFile[]
  selected: SkillFile | undefined
  onSelect: (path: string) => void
}) {
  return (
    <nav className={styles.fileList} aria-label="技能文件">
      {props.files.map((file) => (
        <Button
          className={styles.fileButton}
          color="inherit"
          key={file.path}
          startIcon={<DescriptionOutlinedIcon />}
          variant={props.selected?.path === file.path ? 'outlined' : 'text'}
          onClick={() => props.onSelect(file.path)}
        >
          <span>{file.path}</span>
        </Button>
      ))}
    </nav>
  )
}
