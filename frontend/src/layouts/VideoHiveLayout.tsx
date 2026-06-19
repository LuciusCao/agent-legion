import { useNavigate, Outlet } from 'react-router-dom'
import { IconButton } from '@mui/material'
import { AppShell } from './AppShell'
import { AppBar } from '../components/AppBar'
import { AgentStatusIndicator } from '../components/AgentStatusIndicator'
import { MaterialIcon } from '../components/MaterialIcon'

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
              <AgentStatusIndicator workspaceId="video-hive" />
              <IconButton
                size="small"
                aria-label="设置"
                onClick={() => navigate('/video-hive/settings')}
              >
                <MaterialIcon name="settings" />
              </IconButton>
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
