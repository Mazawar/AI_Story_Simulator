// 叙事块渲染：TurnPayload.narrative 中的块 → React 组件
// 块类型：narration / dialogue / broadcast / choices / note（note 为前端本地系统提示）

import { useMemo } from 'react'
import { Popover } from 'antd'

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** 实体链接文本：已知角色名可点击，弹出 Inspector 卡 */
export function EntityText({ text, entities }) {
  const parts = useMemo(() => {
    if (!entities || !entities.length || !text) return null
    const names = entities.map((e) => e.name).sort((a, b) => b.length - a.length)
    const re = new RegExp('(' + names.map(escapeRegExp).join('|') + ')', 'g')
    return text.split(re)
  }, [text, entities])

  if (!parts) return <>{text}</>

  return (
    <>
      {parts.map((part, i) => {
        const entity = entities.find((e) => e.name === part)
        if (!entity) return <span key={i}>{part}</span>
        return (
          <Popover
            key={i}
            trigger="click"
            placement="top"
            overlayClassName="entity-pop"
            content={
              <div className="entity-card">
                <div className="entity-card-name">{entity.name}</div>
                <div className="entity-card-desc">{entity.desc}</div>
              </div>
            }
          >
            <span className="entity-link">{part}</span>
          </Popover>
        )
      })}
    </>
  )
}

export function BlockView({ block, onChoice, entities }) {
  switch (block.type) {
    case 'narration':
      return <p className="narration-text"><EntityText text={block.text} entities={entities} /></p>

    case 'dialogue':
      return (
        <blockquote className="dialogue">
          <span className="dialogue-speaker">{block.speaker}：</span>
          <EntityText text={block.text} entities={entities} />
        </blockquote>
      )

    case 'broadcast':
      return (
        <div className="broadcast-bar">
          {block.fields.map((f, i) =>
            f.value ? (
              <Popover
                key={i}
                trigger="click"
                placement="top"
                overlayClassName="entity-pop"
                content={
                  <div className="entity-card">
                    <div className="entity-card-name">{f.label}</div>
                    <div className="entity-card-desc">{f.value}</div>
                  </div>
                }
              >
                <span className="broadcast-field broadcast-field-click">
                  <span className="broadcast-label">{f.label}</span>
                  <span className="broadcast-value">{f.value}</span>
                </span>
              </Popover>
            ) : (
              <span className="broadcast-field" key={i}>
                <span className="broadcast-label">{f.label}</span>
              </span>
            )
          )}
        </div>
      )

    case 'choices':
      return (
        <div className="choices-grid">
          {block.options.map((opt) => (
            <button
              key={opt.id}
              className="choice-card"
              onClick={() => onChoice && onChoice(opt)}
              disabled={onChoice == null}
            >
              <span className="choice-id">{opt.id}</span>
              <span className="choice-text"><EntityText text={opt.text} entities={entities} /></span>
            </button>
          ))}
        </div>
      )

    case 'panel':
      return (
        <div className="panel-block">
          <div className="panel-block-title">『{block.title}』</div>
          <div className="panel-block-grid">
            {(block.fields || []).map((f, i) => (
              <span className="panel-block-field" key={i}>
                <span className="broadcast-label">{f.label}</span>
                <span className="broadcast-value">{f.value}</span>
              </span>
            ))}
          </div>
        </div>
      )

    case 'deltas':
      return (
        <div className="deltas-row">
          {(block.items || []).map((d, i) => (
            <span key={i} className={d.op === '+' ? 'delta-chip plus' : 'delta-chip minus'}>
              {d.op === '+' ? '▲' : '▼'} {d.ref} {d.op}{d.v}
              {d.reason ? <em className="delta-reason">{d.reason}</em> : null}
            </span>
          ))}
        </div>
      )

    case 'note':
      return <div className="system-note">〔{block.text}〕</div>

    default:
      return <p className="narration-text">{JSON.stringify(block)}</p>
  }
}

export function StreamView({ text }) {
  if (!text) return null
  return (
    <div className="streaming">
      {text}
      <span className="stream-caret" />
    </div>
  )
}
