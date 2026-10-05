import { useState } from 'react'
import endpointCatalog from './apiAccessEndpoints.json'
import { buildCurlExample, buildPythonExample } from './apiAccessSnippets'
import { ApiAccessCopyButton } from './ApiAccessCopyButton'
import styles from './ApiAccess.module.css'

type ExampleLang = 'curl' | 'python'

const EXAMPLES: { id: ExampleLang; label: string }[] = [
  { id: 'curl', label: 'curl' },
  { id: 'python', label: 'Python' },
]

/**
 * 「外部对接」的可调用端点、最小示例与产物下载说明（#870）。
 *
 * 端点清单 apiAccessEndpoints.json 是 docs/workspace-api-tokens.md「权限面」
 * 表的镜像，由 tests/routes/test_api_access_card_contract.py 与文档、后端
 * api-scope 白名单（auth/api_scope_surface.py）、OpenAPI 契约对账，不允许
 * 单边增删。
 */
export function ApiAccessReference({
  workspaceId,
  apiBase,
}: {
  workspaceId: string
  apiBase: string
}) {
  const [lang, setLang] = useState<ExampleLang>('curl')
  const example =
    lang === 'curl'
      ? buildCurlExample({ apiBase, workspaceId })
      : buildPythonExample({ apiBase, workspaceId })
  const prefix = endpointCatalog.prefix.replace('{workspace_id}', workspaceId)

  return (
    <>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <h3 className={styles.heading}>可调用端点</h3>
        </div>
        <p className={styles.endpointPrefix}>
          以下路径均以 <span className={styles.mono}>{prefix}</span>{' '}
          为前缀；清单之外的端点对 API Token 一律拒绝（404 / 403）。
        </p>
        <table className={styles.endpoints}>
          <thead>
            <tr>
              <th scope="col">方法</th>
              <th scope="col">路径</th>
              <th scope="col">作用</th>
            </tr>
          </thead>
          <tbody>
            {endpointCatalog.endpoints.map((endpoint) => (
              <tr
                key={`${endpoint.method} ${endpoint.path}`}
                data-testid="api-access-endpoint"
              >
                <td>
                  <span
                    className={
                      endpoint.method === 'GET'
                        ? styles.method
                        : `${styles.method} ${styles.methodWrite}`
                    }
                  >
                    {endpoint.method}
                  </span>
                </td>
                <td className={styles.endpointPath}>{endpoint.path}</td>
                <td>{endpoint.purpose}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <h3 className={styles.heading}>最小调用示例</h3>
          <div className={styles.row}>
            <div className={styles.tabs} role="tablist" aria-label="示例语言">
              {EXAMPLES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  aria-selected={lang === item.id}
                  className={lang === item.id ? styles.tabActive : styles.tab}
                  onClick={() => setLang(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <ApiAccessCopyButton
              text={example}
              label="复制示例"
              ariaLabel={`复制 ${lang === 'curl' ? 'curl' : 'Python'} 示例`}
            />
          </div>
        </div>
        <pre className={styles.code} data-testid="api-access-example">
          {example}
        </pre>
        <p className={styles.hint}>
          示例已填入本 workspace 的 ID 与 API Base，把占位符换成签发的 API Token
          即可运行。幂等与重试、完整错误码表见仓库文档{' '}
          <code>docs/workspace-api-tokens.md</code>。
        </p>
      </div>

      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <h3 className={styles.heading}>下载产物：直连与 raw</h3>
        </div>
        <ul className={styles.notes}>
          <li>
            产物清单里对象存储条目带 <code>download_url</code>（presigned
            直连地址）与 <code>expires_at</code>
            ：优先直连下载，字节由对象存储直接应答。直连请求
            <strong>不要</strong>带 Authorization 头。
          </li>
          <li>
            <code>download_url</code> 为 null、已过 <code>expires_at</code>{' '}
            或直连失败（含 403）时，重新取清单或回落 raw 端点；raw 需带 Bearer
            token，产物名按路径段 percent-encode。
          </li>
          <li>
            产物可能以 gzip 形态存储（清单 <code>content_encoding</code> 为{' '}
            <code>gzip</code>）：curl 加 <code>--compressed</code>
            ，requests 自动解码；用清单的 <code>content_hash</code> 校验内容。
          </li>
          <li>
            吊销 Token 不会让已签发的直连地址失效，地址在有效期内仍可下载。
          </li>
        </ul>
      </div>
    </>
  )
}
