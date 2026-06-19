import { FC } from 'react'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import AddIcon from '@mui/icons-material/Add'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import ArrowForwardIcon from '@mui/icons-material/ArrowForward'
import BuildCircleIcon from '@mui/icons-material/BuildCircle'
import CheckIcon from '@mui/icons-material/Check'
import CloseIcon from '@mui/icons-material/Close'
import CloudIcon from '@mui/icons-material/Cloud'
import CloudOffIcon from '@mui/icons-material/CloudOff'
import DataObjectIcon from '@mui/icons-material/DataObject'
import DeleteIcon from '@mui/icons-material/Delete'
import DescriptionIcon from '@mui/icons-material/Description'
import DownloadIcon from '@mui/icons-material/Download'
import ErrorIcon from '@mui/icons-material/Error'
import FolderOpenIcon from '@mui/icons-material/FolderOpen'
import ForumIcon from '@mui/icons-material/Forum'
import HomeIcon from '@mui/icons-material/Home'
import InboxIcon from '@mui/icons-material/Inbox'
import Inventory2Icon from '@mui/icons-material/Inventory2'
import LockIcon from '@mui/icons-material/Lock'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import PlayCircleIcon from '@mui/icons-material/PlayCircle'
import RestartAltIcon from '@mui/icons-material/RestartAlt'
import RocketLaunchIcon from '@mui/icons-material/RocketLaunch'
import SaveIcon from '@mui/icons-material/Save'
import ScheduleIcon from '@mui/icons-material/Schedule'
import SettingsIcon from '@mui/icons-material/Settings'
import SkipNextIcon from '@mui/icons-material/SkipNext'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import StreamIcon from '@mui/icons-material/Stream'
import SubtitlesIcon from '@mui/icons-material/Subtitles'
import TextFieldsIcon from '@mui/icons-material/TextFields'
import TimerIcon from '@mui/icons-material/Timer'
import ToggleOnIcon from '@mui/icons-material/ToggleOn'
import { SvgIconProps } from '@mui/material/SvgIcon'

export type MaterialIconName =
  | 'account_tree'
  | 'add'
  | 'arrow_back'
  | 'arrow_forward'
  | 'build_circle'
  | 'check'
  | 'close'
  | 'cloud'
  | 'cloud_off'
  | 'data_object'
  | 'delete'
  | 'description'
  | 'download'
  | 'error'
  | 'folder_open'
  | 'forum'
  | 'home'
  | 'inbox'
  | 'inventory_2'
  | 'lock'
  | 'more_vert'
  | 'play_arrow'
  | 'play_circle'
  | 'restart_alt'
  | 'rocket_launch'
  | 'save'
  | 'schedule'
  | 'settings'
  | 'skip_next'
  | 'smart_toy'
  | 'stream'
  | 'subtitles'
  | 'text_fields'
  | 'timer'
  | 'toggle_on'

const ICON_MAP: Record<MaterialIconName, FC<SvgIconProps>> = {
  account_tree: AccountTreeIcon,
  add: AddIcon,
  arrow_back: ArrowBackIcon,
  arrow_forward: ArrowForwardIcon,
  build_circle: BuildCircleIcon,
  check: CheckIcon,
  close: CloseIcon,
  cloud: CloudIcon,
  cloud_off: CloudOffIcon,
  data_object: DataObjectIcon,
  delete: DeleteIcon,
  description: DescriptionIcon,
  download: DownloadIcon,
  error: ErrorIcon,
  folder_open: FolderOpenIcon,
  forum: ForumIcon,
  home: HomeIcon,
  inbox: InboxIcon,
  inventory_2: Inventory2Icon,
  lock: LockIcon,
  more_vert: MoreVertIcon,
  play_arrow: PlayArrowIcon,
  play_circle: PlayCircleIcon,
  restart_alt: RestartAltIcon,
  rocket_launch: RocketLaunchIcon,
  save: SaveIcon,
  schedule: ScheduleIcon,
  settings: SettingsIcon,
  skip_next: SkipNextIcon,
  smart_toy: SmartToyIcon,
  stream: StreamIcon,
  subtitles: SubtitlesIcon,
  text_fields: TextFieldsIcon,
  timer: TimerIcon,
  toggle_on: ToggleOnIcon,
}

interface MaterialIconProps extends SvgIconProps {
  name: MaterialIconName
}

export const MaterialIcon: FC<MaterialIconProps> = ({ name, ...props }) => {
  const Icon = ICON_MAP[name]
  if (!Icon) {
    console.warn(`Unknown material icon: ${name}`)
    return null
  }
  return <Icon {...props} />
}
