import { useEffect, useState } from 'react'
import { Button, Input, Switch, message } from 'antd'
import { ArrowLeftOutlined, ApiOutlined, SaveOutlined, DownloadOutlined, CheckCircleFilled } from '@ant-design/icons'
import { Progress } from 'antd'
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
        timer = setTimeout(poll, s.download?.running ? 800 : 6000)
      } catch { timer = setTimeout(poll, 6000) }
    }
    poll()
    return () => clearTimeout(timer)
  }, [])

  const downloadModel = async (key) => {
    // 立即乐观反馈：不等服务端往返
    setModelStatus((s) => ({ ...s, download: { running: true, key, done: 0, total: 0, percent: 0 } }))
    try {
      await api('/api/models/download', { method: 'POST', body: JSON.stringify({ key }) })
      message.success({ content: '开始获取模型，可随时查看进度', key: 'dl', duration: 2 })
      // 立即拉一次真实状态（随后高频轮询接管）
      setModelStatus(await api('/api/models/status'))
    } catch (e) {
      message.error('下载启动失败：' + (e.message || e))
    }
  }

  const modelInfo = (key) => modelStatus?.models?.[key] || null
  const dl = modelStatus?.download || {}

  const ModelStatusLine = ({ keyName, sizeGb }) => {
    const info = modelStatus?.models?.[keyName] || null
    const downloading = dl.running && dl.key === keyName
    const pct = dl.total ? Math.min(100, Math.floor((dl.done * 100) / dl.total)) : 0
    const mbDone = (dl.done / 1048576).toFixed(1)
    const mbTotal = dl.total ? (dl.total / 1048576).toFixed(0) : '?'

    if (downloading) {
      return (
        <div className="model-dl-live">
          <Progress type="circle" size={46} percent={pct}
                    strokeColor={{ '0%': '#8fce88', '100%': '#d8b878' }}
                    format={(p) => `${p}%`} />
          <div className="model-dl-live-info">
            <b>正在获取 {sizeGb}</b>
            <span>已下载 {mbDone} / {mbTotal} MB</span>
          </div>
        </div>
      )
    }
    if (info?.exists) {
      return (
        <div className="model-ready-row">
          <CheckCircleFilled className="model-ready-ico" />
          <span>已就绪 · {(info.size / 1073741824).toFixed(1)} GB</span>
        </div>
      )
    }
    // 上次下载失败 → 红字原因 + 重试 + 手动下载指引
    if (dl.error && dl.key === keyName && !dl.running) {
      return (
        <div className="model-dl-fail">
          <div className="model-dl-fail-msg">下载失败：{dl.error}</div>
          <div className="model-dl-fail-actions">
            <button className="model-dl-btn" onClick={(e) => { e.stopPropagation(); downloadModel(keyName) }}>
              重试（已切换下载源）
            </button>
            <a className="model-dl-manual" target="_blank" rel="noreferrer"
               href="https://hf-mirror.com/unsloth/Qwen_Qwen3-1.7B-GGUF"
               onClick={(e) => e.stopPropagation()}>
              手动下载 → 放入 models/
            </a>
          </div>
        </div>
      )
    }
    const partialMb = info && info.size > 1024 * 1024
      ? `（已传 ${(info.size / 1048576).toFixed(0)} MB，断点续传）` : ''
    return (
      <button className="model-dl-btn"
              onClick={(e) => { e.stopPropagation(); downloadModel(keyName) }}>
        <DownloadOutlined /> {partialMb ? `继续下载${partialMb}` : `获取模型（${sizeGb}）`}
      </button>
    )
  }

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
              <ModelStatusLine keyName="qwen3-1.7b" sizeGb="1.1 GB" />
            </div>
          </label>
          <label className="radio-opt">
            <input type="radio" checked={cfg.model_choice === '4b'}
                   onChange={() => setCfg({ ...cfg, model_choice: '4b' })} />
            <div>
              <b>增强档 · Qwen3-4B</b>
              <p>叙事质量更高；CPU 推理较慢</p>
              <ModelStatusLine keyName="qwen3-4b" sizeGb="2.5 GB" />
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
