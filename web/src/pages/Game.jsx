import { useEffect, useMemo, useRef, useState } from 'react'
import { Input, Button, Tag, Typography, message } from 'antd'
import { SendOutlined, HomeOutlined } from '@ant-design/icons'
import { api, sseUrl } from '../api.js'
import { BlockView, StreamView } from '../components/blocks.jsx'

const { Text } = Typography

const TRIGGERS = ['存档', '读取存档', '修士', '任务', '提示', '本章结束']

function packParam() {
  const q = new URLSearchParams(location.hash.split('?')[1] || '')
  return q.get('pack') || ''
}

const WAITING_HINTS = [
  '命运正在推演……',
  '世界正在苏醒……',
  '因果正在交织……',
  '群山的影子落下来了……',
]

export default function Game() {
  const packTitle = useMemo(packParam, [])
  const [pid, setPid] = useState(null)
  const [blocks, setBlocks] = useState([])      // 已完成回合的渲染块
  const [stream, setStream] = useState('')      // 当前回合流式文本
  const [busy, setBusy] = useState(false)
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const endRef = useRef(null)
  const openedRef = useRef(false)               // 开场只发一次（StrictMode 双挂载防护）
  const pidRef = useRef(null)                   // onopen 回调里读，避免闭包拿到旧 state

  const send = async (text) => {
    const target = pidRef.current
    if (!text.trim() || !target || busy) return
    if (text !== '开始') {
      setBlocks((b) => [...b, { type: 'note', text: '你 › ' + text }])
    }
    setBusy(true)
    setElapsed(0)
    try {
      await api(`/api/play/${target}/input`, {
        method: 'POST',
        body: JSON.stringify({ text }),
      })
    } catch (e) {
      setBusy(false)
      message.error('提交失败：' + (e.message || e))
    }
  }

  useEffect(() => {
    let es = null
    ;(async () => {
      try {
        const r = await api('/api/play', {
          method: 'POST',
          body: JSON.stringify({ pack_title: packTitle }),
        })
        pidRef.current = r.playthrough_id
        setPid(r.playthrough_id)

        es = new EventSource(sseUrl(`/api/play/${r.playthrough_id}/events`))
        es.onopen = () => {
          if (!openedRef.current) {
            openedRef.current = true
            send('开始')                       // 自动触发剧本包「首轮输出」
          }
        }
        es.onmessage = (e) => {
          const ev = JSON.parse(e.data)
          if (ev.type === 'delta') {
            setStream((s) => s + ev.text)
          } else if (ev.type === 'turn') {
            setBlocks((b) => [...b, ...(ev.payload.narrative || [])])
            setStream('')
            setBusy(false)
          } else if (ev.type === 'note') {
            setBlocks((b) => [...b, { type: 'note', text: ev.payload.system_note }])
            setStream('')
            setBusy(false)
          } else if (ev.type === 'error') {
            message.error(ev.message)
            setStream('')
            setBusy(false)
          }
        }
        es.onerror = () => { /* 断线由 history 兜底 */ }
      } catch (e) {
        setError(String(e.message || e))
      }
    })()
    return () => { if (es) es.close() }
  }, [])

  // 生成中：计时 + 提示轮换
  useEffect(() => {
    if (!busy) return
    const t = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [busy])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [blocks, stream])

  const hint = WAITING_HINTS[Math.min(Math.floor(elapsed / 8), WAITING_HINTS.length - 1)]

  if (error) {
    return (
      <div className="game-error">
        <div className="game-error-mark">✕</div>
        <p>创建对局失败：{error}</p>
        <Button onClick={() => { location.hash = '#/' }}>返回剧本架</Button>
      </div>
    )
  }

  const empty = blocks.length === 0 && !stream

  return (
    <div className="game">
      <header className="game-header">
        <button className="icon-btn" onClick={() => { location.hash = '#/' }} title="返回剧本架">
          <HomeOutlined />
        </button>
        <div className="game-title">
          <span className="game-title-main">{packTitle}</span>
          <span className="game-title-sub">命运模拟 · 进行中</span>
        </div>
        {pid && <Tag className="tag-ink">对局 #{pid}</Tag>}
        {busy
          ? <Tag className="tag-live" color="processing">推演 {elapsed}s</Tag>
          : <Tag className="tag-ink">静候行动</Tag>}
      </header>

      <main className="game-narrative">
        {empty && busy && (
          <div className="opening-wait">
            <div className="opening-seal">命</div>
            <p className="opening-hint">{hint}</p>
            <p className="opening-sub">
              本地模型首次推演需加载整卷剧本（约一至两分钟），后续回合会快得多
            </p>
          </div>
        )}
        {blocks.map((b, i) => (
          <BlockView key={i} block={b}
                     onChoice={busy ? null : (opt) => send(opt.text)} />
        ))}
        <StreamView text={stream} />
        {busy && !stream && !empty && (
          <div className="streaming waiting">{hint}<span className="stream-caret" /></div>
        )}
        <div ref={endRef} />
      </main>

      <footer className="game-input">
        <div className="trigger-row">
          {TRIGGERS.map((t) => (
            <button key={t} className="trigger-chip" disabled={!pid || busy}
                    onClick={() => send(t)}>{t}</button>
          ))}
        </div>
        <div className="input-row">
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="写下你的行动……（Enter 发送，Shift+Enter 换行）"
            autoSize={{ minRows: 1, maxRows: 4 }}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault()
                send(input)
                setInput('')
              }
            }}
            disabled={!pid || busy}
            variant="filled"
          />
          <Button type="primary" icon={<SendOutlined />} disabled={!pid || busy}
                  onClick={() => { send(input); setInput('') }}>
            行动
          </Button>
        </div>
      </footer>
    </div>
  )
}
