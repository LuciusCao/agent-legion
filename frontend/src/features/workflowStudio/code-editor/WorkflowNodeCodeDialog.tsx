import { useMemo } from 'react'
import { Close } from '@mui/icons-material'
import { Dialog, IconButton, Toolbar, Tooltip, Typography } from '@mui/material'
import { splitTokensByLine, tokenizePython } from '../../../lib/pythonHighlight'
import styles from './WorkflowNodeCodeDialog.module.css'

type Props = {
  open: boolean
  title: string
  code: string
  onClose: () => void
}

/** 节点代码宽视图：全屏 dialog，等宽字体 + 行号 + 简易语法高亮。
 * 交互模式仿 WorkflowDagFullscreenDialog。 */
export function WorkflowNodeCodeDialog({ open, title, code, onClose }: Props) {
  const lines = useMemo(() => splitTokensByLine(tokenizePython(code)), [code])
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullScreen
      aria-labelledby="node-code-wide-view-title"
    >
      <Toolbar className={styles.toolbar}>
        <Typography
          id="node-code-wide-view-title"
          variant="h6"
          component="div"
          className={styles.title}
        >
          {title}
        </Typography>
        <Tooltip title="关闭">
          <IconButton edge="end" onClick={onClose} aria-label="关闭代码宽视图">
            <Close />
          </IconButton>
        </Tooltip>
      </Toolbar>
      <div className={styles.codeBody}>
        <div className={styles.code} role="code">
          {lines.map((line, lineIndex) => (
            <div key={lineIndex} className={styles.line}>
              <span className={styles.lineNo}>{lineIndex + 1}</span>
              <span className={styles.lineText}>
                {line.map((token, tokenIndex) =>
                  token.kind === 'plain' ? (
                    <span key={tokenIndex}>{token.text}</span>
                  ) : (
                    <span key={tokenIndex} className={styles[token.kind]}>
                      {token.text}
                    </span>
                  )
                )}
              </span>
            </div>
          ))}
        </div>
      </div>
    </Dialog>
  )
}
