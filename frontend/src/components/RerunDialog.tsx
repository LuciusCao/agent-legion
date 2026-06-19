import { useState, useCallback } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  List,
  ListItemButton,
  Radio,
  RadioGroup,
} from '@mui/material'
import { useUiStore } from '../stores/uiStore'
import { KNOWLEDGE_PHASES, QUESTION_PHASES, PHASE_LABELS } from '../labels'
import type { VideoItem } from '../types'

interface RerunDialogProps {
  video: VideoItem | null
  onConfirm: (phase: string) => void
}

function getPhaseSequence(contentType: string): string[] {
  return contentType === 'question' ? QUESTION_PHASES : KNOWLEDGE_PHASES
}

function getAvailablePhases(video: VideoItem | null): string[] {
  if (!video) return KNOWLEDGE_PHASES
  const sequence = getPhaseSequence(video.content_type)
  if (video.status === 'completed') {
    return sequence
  }
  const currentIndex = sequence.indexOf(video.current_phase)
  if (currentIndex === -1) {
    return sequence
  }
  return sequence.slice(0, currentIndex + 1)
}

export function RerunDialog({ video, onConfirm }: RerunDialogProps) {
  const { rerunDialogOpen, closeRerunDialog } = useUiStore()
  const availablePhases = video ? getAvailablePhases(video) : KNOWLEDGE_PHASES
  const [selectedPhase, setSelectedPhase] = useState(
    availablePhases[0] || 'download'
  )

  const handleConfirm = useCallback(() => {
    onConfirm(selectedPhase)
    closeRerunDialog()
  }, [selectedPhase, onConfirm, closeRerunDialog])

  return (
    <Dialog open={rerunDialogOpen} onClose={closeRerunDialog} maxWidth="xs">
      <DialogTitle>选择重跑阶段</DialogTitle>
      <DialogContent>
        <FormControl fullWidth>
          <RadioGroup
            value={selectedPhase}
            onChange={(e) => setSelectedPhase(e.target.value)}
          >
            <List disablePadding>
              {availablePhases.map((phase) => (
                <ListItemButton
                  key={phase}
                  onClick={() => setSelectedPhase(phase)}
                  dense
                >
                  <FormControlLabel
                    value={phase}
                    control={<Radio />}
                    label={PHASE_LABELS[phase]}
                    onClick={(e) => e.stopPropagation()}
                  />
                </ListItemButton>
              ))}
            </List>
          </RadioGroup>
        </FormControl>
      </DialogContent>
      <DialogActions>
        <Button onClick={closeRerunDialog} variant="text">
          取消
        </Button>
        <Button onClick={handleConfirm} variant="contained">
          确认
        </Button>
      </DialogActions>
    </Dialog>
  )
}
