import { useEffect, useMemo, useRef, useState } from 'react'
import { Input, Button, Tag, Typography, message } from 'antd'
import { SendOutlined, HomeOutlined } from '@ant-design/icons'
import { api, sseUrl } from '../api.js'
import { BlockView, StreamView } from '../components/blocks.jsx'

const { Text } = Typography

const BASE_TRIGGERS = ['存档', '读取存档', '继续', '提示', '本章结束']

function playParams() {
  const q = new URLSearchParams(location.hash.split('?')[1] || '')
  return { pack: q.get('pack') || '', pid: q.get('pid') }
}

const WAITING_HINTS = [
  '命运正在推演……',
  '世界正在苏醒……',
  '因果正在交织……',
  '群山的影子落下来了……',
]

/** 角色创建向导：剧本包「首轮输出」的分步选择 */
function CreationWizard({ steps, onDone }) {
  const [idx, setIdx] = useState(0)
  const [choices, setChoices] = useState([])
  const step = steps[idx]

  const choose = (opt) => {
    const next = [...choices, opt]
    if (idx + 1 >= steps.length) {
      const text = steps
        .map((s, i) => {
          const c = next[i]
          const brief = c.text.length > 24 ? c.text.slice(0, 24) + '…' : c.text
          return `${i + 1}.${c.id}（${brief}）`
        })
        .join('；')
      onDone(`【人物已定】${text}。以此身入局，开始剧情。`)
    } else {
      setChoices(next)
      setIdx(idx + 1)
    }
  }

  return (
    <div className="wizard">
      <div className="wizard-head">
        <span className="wizard-title">此身入局</span>
        <span className="wizard-sub">第 {idx + 1} / {steps.length} 抉择</span>
      </div>
      <div className="wizard-steps">
        {steps.map((s, i) => (
          <span key={i} className={i === idx ? 'wizard-dot on' : i < idx ? 'wizard-dot done' : 'wizard-dot'} />
        ))}
      </div>
      <p className="wizard-question">{step.question}</p>
      <div className="wizard-options">
        {step.options.map((opt) => (
          <button key={opt.id} className="choice-card" onClick={() => choose(opt)}>
            <span className="choice-id">{opt.id}</span>
            <span className="choice-text">{opt.text}</span>
          </button>
        ))}
      </div>
      {idx > 0 && (
        <button className="wizard-back" onClick={() => { setIdx(idx - 1); setChoices(choices.slice(0, -1)) }}>
          ← 回上一步
        </button>
      )}
    </div>
  )
}

export default function Game() {
  const params = useMemo(playParams, [])
  const [pid, setPid] = useState(null)
  const [meta, setMeta] = useState(null)          // 当前剧本包元数据（角色卡/创建步骤）
  const [blocks, setBlocks] = useState([])
  const [stream, setStream] = useState('')
  const [busy, setBusy] = useState(false)
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [wizarding, setWizarding] = useState(false)
  const [hudOpen, setHudOpen] = useState(true)
  const [panelWord, setPanelWord] = useState('状态')
  const [playerRole, setPlayerRole] = useState('')
  const [backendName, setBackendName] = useState('')
  const [hud, setHud] = useState(null)   // 最新播报条字段 → 右上角悬浮卡
  const endRef = useRef(null)
  const openedRef = useRef(false)
  const pidRef = useRef(null)

  const send = async (text) => {
    const target = pidRef.current
    if (!text.trim() || !target || busy) return
    if (text !== '开始' && !text.startsWith('【人物已定】')) {
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
        // 剧本包元数据（角色卡 + 创建步骤）；Library 传完整标题，此处兼容子串
        const packs = (await api('/api/packs')).packs
        const packMeta = packs.find((p) => p.title === params.pack)
          || packs.find((p) => p.title.includes(params.pack))
        setMeta(packMeta || null)

        // 新对局 or 续玩
        let r
        if (params.pid) {
          r = await api(`/api/play/${params.pid}/resume`, { method: 'POST' })
          message.info(`已恢复对局（${r.turn_count} 回合）`)
        } else {
          r = await api('/api/play', {
            method: 'POST',
            body: JSON.stringify({ pack_title: params.pack }),
          })
        }
        pidRef.current = r.playthrough_id
        setPid(r.playthrough_id)
        if (r.panel_word) setPanelWord(r.panel_word)
        if (r.player_role) setPlayerRole(r.player_role)
        setBackendName(r.backend)

        es = new EventSource(sseUrl(`/api/play/${r.playthrough_id}/events`))
        es.onopen = () => {
          if (openedRef.current) return
          openedRef.current = true
          if (!params.pid) {
            const steps = packMeta?.creation_steps || []
            if (steps.length >= 1) {
              setWizarding(true)                  // 向导流程，完成后由向导发首条消息
            } else {
              send('开始')                        // 无分步设定的包：直接开场
            }
          }
        }
        es.onmessage = (e) => {
          const ev = JSON.parse(e.data)
          if (ev.type === 'delta') {
            setStream((s) => s + ev.text)
          } else if (ev.type === 'turn') {
            const p = ev.payload
            const extra = []
            // 播报条从叙事流剥离 → 右上角 HUD
            const bcBlock = (p.narrative || []).find((b) => b.type === 'broadcast')
            if (bcBlock) setHud(bcBlock.fields)
            const flowBlocks = (p.narrative || []).filter((b) => b.type !== 'broadcast')
            if (p.choices && p.choices.length) {
              extra.push({ type: 'choices', options: p.choices })
            }
            const all = p.deltas || []
            const numeric = all.filter((d) =>
              (d.ref === '灵石' || d.ref === '修为' ||
               String(d.ref).startsWith('item:')) && d.ref !== '地点')
            const progress = all.filter((d) => String(d.ref).startsWith('flag:') || d.ref === 'anchor')
            if (numeric.length) {
              extra.push({ type: 'deltas', items: numeric })
            }
            if (progress.length) {
              const seen = new Set()
              const names = []
              for (const d of progress) {
                const label = String(d.ref).replace(/^(flag:|anchor$)/, '').trim()
                const key = d.ref + '|' + d.reason
                if (label && !seen.has(key)) { seen.add(key); names.push(d.reason || label) }
              }
              if (names.length) {
                extra.push({ type: 'note', text: '剧情推进：' + [...new Set(names)].join('、') })
              }
            }
            setBlocks((b) => [...b, { type: 'divider' }, ...flowBlocks, ...extra])
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

  useEffect(() => {
    if (!busy) return
    const t = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [busy])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [blocks, stream, wizarding])

  const hint = WAITING_HINTS[Math.min(Math.floor(elapsed / 8), WAITING_HINTS.length - 1)]
  const entities = meta?.characters || []
  // 只有最新一批选项可点：历史选项置灰，防止点旧选项扰乱剧情
  const lastChoicesIdx = (() => {
    for (let i = blocks.length - 1; i >= 0; i--) {
      if (blocks[i].type === 'choices') return i
    }
    return -1
  })()

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
      {hud && hud.length > 0 && (
        <aside className="hud-card" onClick={() => setHudOpen((v) => !v)}>
          <div className="hud-title">◈ 状态</div>
          {playerRole && <div className="hud-role">扮演：{playerRole}</div>}
          <div className={"hud-fields" + (hudOpen ? '' : ' collapsed')}>
            {hud.map((f, i) => (
              <span className="hud-field" key={i}>
                <span className="hud-label">{f.label}</span>
                <span className="hud-value">{f.value}</span>
              </span>
            ))}
          </div>
        </aside>
      )}
      <header className="game-header">
        <button className="icon-btn" onClick={() => { location.hash = '#/' }} title="返回剧本架">
          <HomeOutlined />
        </button>
        <div className="game-title">
          <span className="game-title-main">{params.pack}</span>
          <span className="game-title-sub">{params.pid ? '续玩 · ' : ''}命运模拟 · 进行中</span>
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
              {backendName === 'remote'
                ? '在线模型推演中，通常数十秒内完成'
                : '本地模型首次推演需加载剧本（约一至两分钟），后续回合会快得多'}
            </p>
          </div>
        )}

        {wizarding && !busy && (
          <CreationWizard
            steps={meta.creation_steps}
            onDone={(text) => { setWizarding(false); send(text) }}
          />
        )}

        {blocks.map((b, i) => {
          if (b.type === 'choices' && i !== lastChoicesIdx) return null  // 历史选项不渲染
          return (
            <BlockView key={i} block={b} entities={entities}
                       onChoice={(!busy && i === lastChoicesIdx)
                         ? (opt) => send(opt.text) : null} />
          )
        })}
        <StreamView text={stream} />
        {busy && !stream && !empty && (
          <div className="streaming waiting">{hint}<span className="stream-caret" /></div>
        )}
        <div ref={endRef} />
      </main>

      <footer className="game-input">
        <div className="trigger-row">
          {[BASE_TRIGGERS[0], BASE_TRIGGERS[1], panelWord,
            ...BASE_TRIGGERS.slice(2)].map((t) => (
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
            disabled={!pid || busy || wizarding}
            variant="filled"
          />
          <Button type="primary" icon={<SendOutlined />} disabled={!pid || busy || wizarding}
                  onClick={() => { send(input); setInput('') }}>
            行动
          </Button>
        </div>
      </footer>
    </div>
  )
}
