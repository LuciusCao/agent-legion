import React, { useMemo } from 'react'
import { List, ListItemButton, ListItemText } from '@mui/material'
import { useArtifactStore } from '../stores/artifactStore'
import { LaTeXText } from './LaTeXText'

export const SubtitlePanel = React.memo(function SubtitlePanel({
  currentTime,
  onSeek,
}: {
  currentTime: number
  onSeek: (time: number) => void
}) {
  const { artifacts } = useArtifactStore()
  const subtitles = artifacts.subtitles

  const activeIndex = useMemo(() => {
    return subtitles.findIndex(
      (s) => currentTime >= s.start && currentTime < s.end
    )
  }, [subtitles, currentTime])

  return (
    <List className="tab-panel" disablePadding>
      {subtitles.map((sub, i) => (
        <ListItemButton
          key={i}
          selected={i === activeIndex}
          onClick={() => onSeek(sub.start)}
          dense
        >
          <ListItemText
            primary={
              <span
                style={{
                  fontVariantNumeric: 'tabular-nums',
                  minWidth: '100px',
                }}
              >
                {formatTime(sub.start)} → {formatTime(sub.end)}
              </span>
            }
            secondary={<LaTeXText>{sub.text}</LaTeXText>}
          />
        </ListItemButton>
      ))}
    </List>
  )
})

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
