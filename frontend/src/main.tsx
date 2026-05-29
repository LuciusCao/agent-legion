import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { applyTheme } from './theme'
import '@material/web/all.js'
import '@material/web/labs/segmentedbuttonset/outlined-segmented-button-set.js'
import '@material/web/labs/segmentedbutton/outlined-segmented-button.js'
import App from './App'
import './styles.css'

applyTheme()

const root = document.getElementById('app')
if (!root) throw new Error('App root not found')

createRoot(root).render(
  <StrictMode>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <App />
    </BrowserRouter>
  </StrictMode>
)
