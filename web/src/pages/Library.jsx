import { useEffect, useState } from 'react'
import { Button } from 'antd'
import { PlayCircleOutlined, ReloadOutlined, StepForwardOutlined, SettingOutlined,
         LeftOutlined, RightOutlined } from '@ant-design/icons'
import { useState as usePageState } from 'react'
import { api } from '../api.js'

const PACK_META = {
  凡人: { desc: '人界五境 · 凡人流修仙', tone: '墨绿' },
  剑来: { desc: '骊珠洞天 · 小镇少年', tone: '黛青' },
  完美: { desc: '下界八域 · 猎凶证道', tone: '赤金' },
}

function metaOf(title) {
  for (const key of Object.keys(PACK_META)) {
    if (title.includes(key)) return PACK_META[key]
  }
  return { desc: '剧情模拟 · 世界运行中', tone: '墨绿' }
}

function Pager({ page, total, onPage }) {
  if (total <= 1) return null
  return (
    <div className="pager">
      <button className="pager-btn" disabled={page === 0} onClick={() => onPage(page - 1)}>
        <LeftOutlined />
      </button>
      <span className="pager-dots">
        {Array.from({ length: total }).map((_, i) => (
          <span key={i} className={i === page ? 'pager-dot on' : 'pager-dot'} />
        ))}
      </span>
      <button className="pager-btn" disabled={page === total - 1} onClick={() => onPage(page + 1)}>
        <RightOutlined />
      </button>
    </div>
  )
}

function chunk(arr, n) {
  const out = []
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n))
  return out
}

export default function Library() {
  const [packs, setPacks] = useState(null)
  const [plays, setPlays] = useState([])
  const [loading, setLoading] = useState(false)
  const [playPage, setPlayPage] = useState(0)
  const [packPage, setPackPage] = useState(0)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [packData, playData] = await Promise.all([api('/api/packs'), api('/api/plays')])
      setPacks(packData.packs)
      setPlays(playData.plays || [])
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const enter = (title) => {
    location.hash = '#/play?pack=' + encodeURIComponent(title)
  }
  const resume = (title, pid) => {
    location.hash = '#/play?pack=' + encodeURIComponent(title) + '&pid=' + pid
  }

  return (
    <div className="library">
      <section className="library-hero">
        <div className="library-hero-seal">演</div>
        <div>
          <h1 className="library-hero-title">AI 剧情模拟器</h1>
          <p className="library-hero-sub">
            投身既定的命运，改写未定的结局——由本地小模型主持的单机人生模拟
          </p>
        </div>
        <div className="library-tools">
          <button className="icon-btn" onClick={() => { location.hash = '#/settings' }} title="设置">
            <SettingOutlined />
          </button>
          <button className="icon-btn" onClick={load} title="刷新">
            <ReloadOutlined spin={loading} />
          </button>
        </div>
      </section>

      {error && (
        <div className="library-error">
          无法连接本地服务（{error}）。请确认服务已启动且 token 正确。
        </div>
      )}

      {plays.length > 0 && (
        <section className="resume-section">
          <h2 className="resume-title">未竟之局 · {plays.length}</h2>
          <Pager page={playPage} total={Math.ceil(plays.length / 3)}
                 onPage={setPlayPage} />
          <div className="strip-page">
            {chunk(plays, 3)[Math.min(playPage, Math.ceil(plays.length / 3) - 1)].map((p) => (
              <div key={p.id} className="resume-card">
                <div className="resume-card-top">
                  <span className="resume-card-badge">{p.mode === 'engine' ? '引擎' : '直通'}</span>
                  {p.save_summary && <span className="resume-card-saved">已存档</span>}
                </div>
                <div className="resume-card-title">{p.story_title}</div>
                <div className="resume-card-meta">第 {p.turn_count} 回合 · {p.updated_at?.slice(5, 16)}</div>
                <div className="resume-card-actions">
                  <button className="resume-btn primary"
                          onClick={() => resume(p.story_title, p.id)}>继续推演</button>
                  <button className="resume-btn"
                          onClick={() => enter(p.story_title)}>全新开局</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <Pager page={packPage} total={Math.ceil((packs || []).length / 3)}
             onPage={setPackPage} />
      <div className="strip-page">
        {chunk((packs || []), 3)[Math.min(packPage, Math.max(0, Math.ceil((packs || []).length / 3) - 1))]?.map((p) => {
          const m = metaOf(p.title)
          const hasWizard = (p.creation_steps || []).length > 0
          return (
            <article key={p.title} className="pack-card" onClick={() => enter(p.title)}>
              <div className="pack-card-tone" data-tone={m.tone} />
              <div className="pack-card-body">
                <div className="pack-card-tone-label">{m.tone}</div>
                <h2 className="pack-card-title">{p.title}</h2>
                <p className="pack-card-desc">{m.desc}</p>
                <div className="pack-card-meta">
                  <span>{p.sections} 卷</span>
                  <span className="dot" />
                  <span>{(p.chars / 10000).toFixed(1)} 万字</span>
                  <span className="dot" />
                  <span>{(p.characters || []).length} 位角色</span>
                  {hasWizard && (
                    <>
                      <span className="dot" />
                      <span>创建向导</span>
                    </>
                  )}
                </div>
                <Button type="primary" ghost icon={<PlayCircleOutlined />}
                        className="pack-card-btn"
                        onClick={(e) => { e.stopPropagation(); enter(p.title) }}>
                  入局
                </Button>
              </div>
            </article>
          )
        })}
        {packs && packs.length === 0 && (
          <div className="library-error">script/ 目录下没有剧本包</div>
        )}
      </div>

      <footer className="library-footer">
        剧本包即世界 —— 放入新的设定文档，即可开启新的人生
      </footer>
    </div>
  )
}
