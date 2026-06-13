import { useNavigate, Outlet } from 'react-router-dom'
import { AppShell } from './AppShell'
import { AppBar } from '../components/AppBar'

export default function VideoHiveLayout() {
  const navigate = useNavigate()

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title="Video Hive"
          home
          scrolled={scrolled}
          rightActions={
            <>
              <md-icon-button
                aria-label="设置"
                onClick={() => navigate('/video-hive/settings')}
              >
                <md-icon>settings</md-icon>
              </md-icon-button>
            </>
          }
        />
      )}
      mainClassName="video-hive-main"
    >
      <Outlet />
    </AppShell>
  )
}
