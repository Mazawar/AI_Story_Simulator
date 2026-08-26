import { useEffect, useMemo, useRef, useState } from 'react'
import { Input, Button, Space, Tag, Typography, message } from 'antd'
import { SendOutlined, HomeOutlined } from '@ant-design/icons'
import { api, sseUrl } from '../api.js'
import { BlockView, StreamView } from '../components/blocks.jsx'

const { Text } = Typography

const TRIGGERS = ['存档', '读取存档', '修士', '任务', '提示', '本章结束']

function packParam() {
  const q = new URLSearchParams(location.hash.split('?')[1] || '')
  return q.get('pack') || ''
}

export default function Game() {
  const packTitle = useMemo(packParam, [])
  const [pid, setPid] = useState(null)
  const [blocks, setBlocks] = useState([])      // 已完成回合的渲染块
  const [stream, setStream] = useState('')      // 当前回合流式文本
  const [busy, setBusy] = useState(false)
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const endRef = useRef(null)

  const send = async (text) => {
    if (!text.trim() || !pid || busy) return
    setBlocks((b) => [...b, { type: 'note', text: '你> ' + text }])
    setBusy(true)
    try {
      await api(`/api/play/${pid}/input`, {
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
        setPid(r.playthrough_id)
        message.success(`对局已创建（${r.pack_title} · ${r.backend} 后端）`)

        es = new EventSource(sseUrl(`/api/play/${r.playthrough_id}/events`))
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
        es.onerror = () => { /* 断线由 history 兜底，不弹错误 */ }
      } catch (e) {
        setError(String(e.message || e))
      }
    })()
    return () => { if (es) es.close() }
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [blocks, stream])

  if (error) {
    return (
      <div className="game-error">
        <p>创建对局失败：{error}</p>
        <Button onClick={() => { location.hash = '#/' }}>返回剧本架</Button>
      </div>
    )
  }

  return (
    <div className="game">
      <header className="game-header">
        <Button icon={<HomeOutlined />} size="small" onClick={() => { location.hash = '#/' }} />
        <Text strong>{packTitle}</Text>
        <Tag>{pid ? `对局 #${pid}` : '创建中…'}</Tag>
        {busy && <Tag color="processing">生成中</Tag>}
      </header>

      <main className="game-narrative">
        {blocks.map((b, i) => (
          <BlockView key={i} block={b}
                     onChoice={busy ? null : (opt) => send(opt.text)} />
        ))}
        <StreamView text={stream} />
        {busy && !stream && <div className="streaming waiting">……</div>}
        <div ref={endRef} />
      </main>

      <footer className="game-input">
        <Space wrap style={{ marginBottom: 8 }}>
          {TRIGGERS.map((t) => (
            <Button key={t} size="small" disabled={!pid || busy} onClick={() => send(t)}>
              {t}
            </Button>
          ))}
        </Space>
        <div className="input-row">
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入你的行动，或点击上方触发词 / 选项"
            autoSize={{ minRows: 1, maxRows: 4 }}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault()
                send(input)
                setInput('')
              }
            }}
            disabled={!pid || busy}
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
