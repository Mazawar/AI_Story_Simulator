// 叙事块渲染：TurnPayload.narrative 中的块 → React 组件
// 块类型：narration / dialogue / broadcast / choices / note（note 为前端本地系统提示）

export function BlockView({ block, onChoice }) {
  switch (block.type) {
    case 'narration':
      return <p className="narration-text">{block.text}</p>

    case 'dialogue':
      return (
        <blockquote className="dialogue">
          <span className="dialogue-speaker">{block.speaker}：</span>
          {block.text}
        </blockquote>
      )

    case 'broadcast':
      return (
        <div className="broadcast-bar">
          {block.fields.map((f, i) => (
            <span className="broadcast-field" key={i}>
              <span className="broadcast-label">{f.label}</span>
              <span className="broadcast-value">{f.value}</span>
            </span>
          ))}
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
              <span className="choice-text">{opt.text}</span>
            </button>
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
