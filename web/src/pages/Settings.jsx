import { useEffect, useState } from 'react'
import { Button, Input, Switch, message } from 'antd'
import { ArrowLeftOutlined, ApiOutlined, SaveOutlined, DownloadOutlined, CheckCircleFilled } from '@ant-design/icons'
import { api } from '../api.js'

export default function Settings() {
  const [cfg, setCfg] = useState(null)
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [modelStatus, setModelStatus] = useState(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)

  const load = async () => {
    try {
      setCfg(await api('/api/settings'))
    } catch (e) {
      message.error('读取设置失败：' + (e.message || e))
    }
  }
  useEffect(() => { load() }, [])

  // 轮询模型/下载状态（下载进行中高频，空闲低频）
  useEffect(() => {
    let timer = null
    const poll = async () => {
      try {
        const s = await api('/api/models/status')
        setModelStatus(s)
        timer = setTimeout(poll, s.download?.running ? 1200 : 6000)
      } catch { timer = setTimeout(poll, 6000) }
    }
    poll()
    return () => clearTimeout(timer)
  }, [])

  const downloadModel = async (key) => {
    try {
      await api('/api/models/download', { method: 'POST', body: JSON.stringify({ key }) })
      message.info('开始下载（断点续传，关闭页面不中断）')
    } catch (e) {
      message.error('下载启动失败：' + (e.message || e))
    }
  }

  const modelInfo = (key) => modelStatus?.models?.[key] || null
  const dl = modelStatus?.download || {}

  const save = async () => {
    setSaving(true)
    try {
      await api('/api/settings', {
        method: 'POST',
        body: JSON.stringify({
          api_base_url: cfg.api_base_url,
          api_key: apiKey || undefined,     // 空 = 不修改已存密钥
          api_model: cfg.api_model,
          prefer_online: cfg.prefer_online,
          api_allow_private: cfg.api_allow_private,
          model_choice: cfg.model_choice,
        }),
      })
      message.success('设置已保存（下次开局生效）')
      setApiKey('')
      load()
    } catch (e) {
      message.error('保存失败：' + (e.message || e))
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      await save()
      const r = await api('/api/settings/test', { method: 'POST' })
      setTestResult(r)
    } catch (e) {
      setTestResult({ ok: false, message: String(e.message || e) })
    } finally {
      setTesting(false)
    }
  }

  if (!cfg) return <div className="settings"><p className="opening-sub">加载中…</p></div>

  const set = (k) => (e) => setCfg({ ...cfg, [k]: typeof e === 'boolean' ? e : e.target.value })

  return (
    <div className="settings">
      <header className="settings-header">
        <button className="icon-btn" onClick={() => { location.hash = '#/' }} title="返回">
          <ArrowLeftOutlined />
        </button>
        <div className="settings-seal">设</div>
        <div>
          <h1 className="settings-title">设置</h1>
          <p className="settings-sub">模型档位与在线服务</p>
        </div>
      </header>

      <div className="settings-grid">
      <section className="settings-card">
        <h2><span className="card-ico">⚡</span>推理模型档位</h2>
        <div className="settings-row">
          <label className="radio-opt">
            <input type="radio" checked={cfg.model_choice !== '4b'}
                   onChange={() => setCfg({ ...cfg, model_choice: 'local' })} />
            <div>
              <b>主力档 · Qwen3-1.7B</b>
              <p>速度快，推荐日常推演（无此模型文件时自动回落其它已放置模型）</p>
              {(() => {
                const info = modelInfo('qwen3-1.7b'); if (!info) return null
                return info.exists
                  ? <p className="model-ready"><CheckCircleFilled /> 已就绪 · {(info.size / 1048576 / 1024).toFixed(1)} GB</p>
                  : <button className="model-dl-btn" onClick={(e) => { e.stopPropagation(); downloadModel('qwen3-1.7b') }}>
                      <DownloadOutlined /> 下载模型（1.1 GB）</button>
              })()}
            </div>
          </label>
          <label className="radio-opt">
            <input type="radio" checked={cfg.model_choice === '4b'}
                   onChange={() => setCfg({ ...cfg, model_choice: '4b' })} />
            <div>
              <b>增强档 · Qwen3-4B</b>
              <p>叙事质量更高；CPU 推理较慢</p>
              {(() => {
                const info = modelInfo('qwen3-4b'); if (!info) return null
                return info.exists
                  ? <p className="model-ready"><CheckCircleFilled /> 已就绪 · {(info.size / 1048576 / 1024).toFixed(1)} GB</p>
                  : <button className="model-dl-btn" onClick={(e) => { e.stopPropagation(); downloadModel('qwen3-4b') }}>
                      <DownloadOutlined /> 下载模型（2.5 GB）</button>
              })()}
              {dl.running && dl.key === 'qwen3-4b' && (
                <div className="model-dl-progress">
                  <div className="model-dl-bar"><div style={{ width: `${dl.percent}%` }} /></div>
                  <span>{dl.percent}%</span>
                </div>
              )}
            </div>
          </label>
        </div>
      </section>

      <section className="settings-card">
        <h2><span className="card-ico">🌐</span>在线 API（可选）</h2>
        <p className="settings-hint">
          配置任意 OpenAI 兼容接口（官方 / DeepSeek / 智谱 / 本地 Ollama…）。
          启用后推演走在线模型，连接失败自动回落本地。
        </p>
        <div className="settings-form">
          <label className="span2">API 地址（以 /v1 结尾）
            <Input value={cfg.api_base_url} onChange={set('api_base_url')}
                   placeholder="https://api.example.com/v1" />
          </label>
          <label>API 密钥 {cfg.api_key_set && <span className="key-mask">已存：{cfg.api_key_masked}</span>}
            <Input.Password value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                            placeholder={cfg.api_key_set ? '留空则不修改' : 'sk-...'} />
          </label>
          <label>模型名
            <Input value={cfg.api_model} onChange={set('api_model')}
                   placeholder="如 deepseek-chat / glm-4-flash" />
          </label>
          <div className="settings-switches span2">
            <label><Switch size="small" checked={cfg.prefer_online} onChange={set('prefer_online')} />
              启用在线优先（关闭 = 纯本地推演）</label>
            <label><Switch size="small" checked={cfg.api_allow_private} onChange={set('api_allow_private')} />
              允许本机/内网端点（Ollama、LM Studio）</label>
          </div>
        </div>
        <div className="settings-actions">
          <Button icon={<ApiOutlined />} loading={testing} onClick={test}>测试连通</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save}>保存设置</Button>
        </div>
        {testResult && (
          <div className={testResult.ok ? 'test-ok' : 'test-fail'}>
            {testResult.ok ? '✓ ' : '✗ '}{testResult.message}
          </div>
        )}
      </section>
      </div>
    </div>
  )
}
