import { Routes, Route } from 'react-router-dom'
import { GlobalSettingsPage, UsersAdminPage } from './pages'

export default function AdminRoutes() {
  return (
    <Routes>
      <Route path="users" element={<UsersAdminPage />} />
      <Route path="settings" element={<GlobalSettingsPage />} />
    </Routes>
  )
}
