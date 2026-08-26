import { useEffect, useState } from 'react'
import { Card, Col, Row, Button, Tag, Typography, message } from 'antd'
import { BookOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api.js'

const { Title, Text } = Typography

export default function Library() {
  const [packs, setPacks] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api('/api/packs')
      setPacks(data.packs)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div className="library">
      <div className="library-header">
        <Title level={3}><BookOutlined /> AI 剧情模拟器</Title>
        <Text type="secondary">选择一个剧本包，开始你的穿越人生</Text>
        <Button style={{ marginLeft: 12 }} icon={<ReloadOutlined />} onClick={load}
                loading={loading} size="small" />
      </div>

      {error && <Text type="danger">{error}（请确认本地服务已启动且 token 正确）</Text>}

      <Row gutter={[16, 16]}>
        {(packs || []).map((p) => (
          <Col xs={24} sm={12} lg={8} key={p.title}>
            <Card
              hoverable
              title={p.title}
              extra={<Tag color="green">直通模式</Tag>}
              onClick={() => { location.hash = '#/play?pack=' + encodeURIComponent(p.title) }}
            >
              <p><Text type="secondary">{p.sections} 个章节 · {p.chars.toLocaleString()} 字</Text></p>
              <Button type="primary" icon={<PlayCircleOutlined />}
                      onClick={(e) => {
                        e.stopPropagation()
                        message.info('正在创建对局…')
                        location.hash = '#/play?pack=' + encodeURIComponent(p.title)
                      }}>
                开始游戏
              </Button>
            </Card>
          </Col>
        ))}
        {packs && packs.length === 0 && (
          <Col span={24}><Card>script/ 目录下没有剧本包</Card></Col>
        )}
      </Row>
    </div>
  )
}
