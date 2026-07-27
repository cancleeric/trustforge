import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { applyReducedMotionAttribute } from './lib/useReducedMotion'

// N63：低動態偏好在 render 之前先套上，避免第一幀先動起來再被關掉。
applyReducedMotionAttribute()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
