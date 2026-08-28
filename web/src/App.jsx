import { useEffect, useState } from 'react'
import Library from './pages/Library.jsx'
import Game from './pages/Game.jsx'
import Settings from './pages/Settings.jsx'

// 极简 hash 路由：#/ → 剧本架；#/play?pack=标题 → 对局
export default function App() {
  const [hash, setHash] = useState(location.hash || '#/')
  useEffect(() => {
    const onChange = () => setHash(location.hash || '#/')
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  if (hash.startsWith('#/play')) {
    return <Game />
  }
  if (hash.startsWith('#/settings')) {
    return <Settings />
  }
  return <Library />
}
