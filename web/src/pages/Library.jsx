import { useEffect, useState } from 'react'
import { Button } from 'antd'
import { PlayCircleOutlined, ReloadOutlined, StepForwardOutlined } from '@ant-design/icons'
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

export default function Library() {
  const [packs, setPacks] = useState(null)
  const [plays, setPlays] = useState([])
  const [loading, setLoading] = useState(false)
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
        <button className="icon-btn library-refresh" onClick={load} title="刷新">
          <ReloadOutlined spin={loading} />
        </button>
      </section>

      {error && (
        <div className="library-error">
          无法连接本地服务（{error}）。请确认服务已启动且 token 正确。
        </div>
      )}

      {plays.length > 0 && (
        <section className="resume-section">
          <h2 className="resume-title">未竟之局</h2>
          <div className="resume-list">
            {plays.map((p) => (
              <button key={p.id} className="resume-card"
                      onClick={() => resume(p.story_title, p.id)}>
                <div className="resume-card-main">
                  <span className="resume-card-title">{p.story_title}</span>
                  <span className="resume-card-meta">
                    第 {p.turn_count} 回合 · {p.updated_at}
                    {p.save_summary ? ' · 已存档' : ''}
                  </span>
                </div>
                <StepForwardOutlined className="resume-card-icon" />
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="library-grid">
        {(packs || []).map((p) => {
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
