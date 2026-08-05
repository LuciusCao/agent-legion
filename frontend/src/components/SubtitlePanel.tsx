import React, { useMemo } from 'react'
import { List, ListItemButton, ListItemText } from '@mui/material'
import type { VideoArtifacts } from '../types'
import { useVideoNodeStore } from '../stores/videoNodeStore'
import { RichText } from './RichText'

export const SubtitlePanel = React.memo(function SubtitlePanel({
  currentTime,
  onSeek,
  subtitles: subtitlesProp,
}: {
  currentTime: number
  onSeek: (time: number) => void
  subtitles?: VideoArtifacts['subtitles']
}) {
  const { artifacts } = useVideoNodeStore()
  const subtitles = subtitlesProp ?? artifacts.subtitles

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
            secondary={<RichText mode="inline">{sub.text}</RichText>}
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
