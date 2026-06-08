import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { applyTheme } from './theme'

// Material Web — 按需导入实际使用的组件
import '@material/web/list/list.js'
import '@material/web/list/list-item.js'
import '@material/web/dialog/dialog.js'
import '@material/web/checkbox/checkbox.js'
import '@material/web/radio/radio.js'
import '@material/web/switch/switch.js'
import '@material/web/tabs/tabs.js'
import '@material/web/tabs/primary-tab.js'
import '@material/web/textfield/outlined-text-field.js'
import '@material/web/button/filled-button.js'
import '@material/web/button/outlined-button.js'
import '@material/web/button/text-button.js'
import '@material/web/chips/assist-chip.js'
import '@material/web/chips/filter-chip.js'
import '@material/web/chips/suggestion-chip.js'
import '@material/web/icon/icon.js'
import '@material/web/iconbutton/icon-button.js'
import '@material/web/labs/segmentedbuttonset/outlined-segmented-button-set.js'
import '@material/web/labs/segmentedbutton/outlined-segmented-button.js'
import '@material/web/select/outlined-select.js'
import '@material/web/select/select-option.js'

import App from './App'
import './styles.css'

applyTheme()

const root = document.getElementById('app')
if (!root) throw new Error('App root not found')

createRoot(root).render(
  <StrictMode>
    <BrowserRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <App />
    </BrowserRouter>
  </StrictMode>
)
