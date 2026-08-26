import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App.jsx'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{
      token: {
        colorPrimary: '#8fce88', colorInfo: '#8fce88',
        colorBgContainer: '#1b2027', colorBgElevated: '#232a33',
        colorText: '#ddd6c4', colorTextSecondary: '#8d8a7c',
        colorBorder: '#2b333d', colorBorderSecondary: '#232a33',
        borderRadius: 10, fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
      },
    }}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
