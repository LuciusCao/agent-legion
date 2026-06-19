import { FC } from 'react'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import AddIcon from '@mui/icons-material/Add'
import AddTaskIcon from '@mui/icons-material/AddTask'
import ArchiveIcon from '@mui/icons-material/Archive'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import ArrowForwardIcon from '@mui/icons-material/ArrowForward'
import BuildCircleIcon from '@mui/icons-material/BuildCircle'
import CheckIcon from '@mui/icons-material/Check'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ChecklistIcon from '@mui/icons-material/Checklist'
import CloseIcon from '@mui/icons-material/Close'
import CloudIcon from '@mui/icons-material/Cloud'
import CloudOffIcon from '@mui/icons-material/CloudOff'
import DataObjectIcon from '@mui/icons-material/DataObject'
import DeleteIcon from '@mui/icons-material/Delete'
import DescriptionIcon from '@mui/icons-material/Description'
import DownloadIcon from '@mui/icons-material/Download'
import ErrorIcon from '@mui/icons-material/Error'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import FolderOpenIcon from '@mui/icons-material/FolderOpen'
import ForumIcon from '@mui/icons-material/Forum'
import HelpIcon from '@mui/icons-material/Help'
import HomeIcon from '@mui/icons-material/Home'
import InboxIcon from '@mui/icons-material/Inbox'
import Inventory2Icon from '@mui/icons-material/Inventory2'
import ListIcon from '@mui/icons-material/List'
import LockIcon from '@mui/icons-material/Lock'
import LockOpenIcon from '@mui/icons-material/LockOpen'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import PlayCircleIcon from '@mui/icons-material/PlayCircle'
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked'
import RestartAltIcon from '@mui/icons-material/RestartAlt'
import RocketLaunchIcon from '@mui/icons-material/RocketLaunch'
import SaveIcon from '@mui/icons-material/Save'
import ScheduleIcon from '@mui/icons-material/Schedule'
import SettingsIcon from '@mui/icons-material/Settings'
import SkipNextIcon from '@mui/icons-material/SkipNext'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import StreamIcon from '@mui/icons-material/Stream'
import SubtitlesIcon from '@mui/icons-material/Subtitles'
import SyncIcon from '@mui/icons-material/Sync'
import TextFieldsIcon from '@mui/icons-material/TextFields'
import TimerIcon from '@mui/icons-material/Timer'
import ToggleOnIcon from '@mui/icons-material/ToggleOn'
import { SvgIconProps } from '@mui/material/SvgIcon'

export type MaterialIconName =
  | 'account_tree'
  | 'add'
  | 'add_task'
  | 'archive'
  | 'arrow_back'
  | 'arrow_forward'
  | 'build_circle'
  | 'check'
  | 'check_circle'
  | 'checklist'
  | 'close'
  | 'cloud'
  | 'cloud_off'
  | 'data_object'
  | 'delete'
  | 'description'
  | 'download'
  | 'error'
  | 'expand_less'
  | 'expand_more'
  | 'folder_open'
  | 'forum'
  | 'help'
  | 'home'
  | 'inbox'
  | 'inventory_2'
  | 'list'
  | 'lock'
  | 'lock_open'
  | 'more_vert'
  | 'play_arrow'
  | 'play_circle'
  | 'radio_button_unchecked'
  | 'restart_alt'
  | 'rocket_launch'
  | 'save'
  | 'schedule'
  | 'settings'
  | 'skip_next'
  | 'smart_toy'
  | 'stream'
  | 'subtitles'
  | 'sync'
  | 'text_fields'
  | 'timer'
  | 'toggle_on'

const ICON_MAP: Record<MaterialIconName, FC<SvgIconProps>> = {
  account_tree: AccountTreeIcon,
  add: AddIcon,
  add_task: AddTaskIcon,
  archive: ArchiveIcon,
  arrow_back: ArrowBackIcon,
  arrow_forward: ArrowForwardIcon,
  build_circle: BuildCircleIcon,
  check: CheckIcon,
  check_circle: CheckCircleIcon,
  checklist: ChecklistIcon,
  close: CloseIcon,
  cloud: CloudIcon,
  cloud_off: CloudOffIcon,
  data_object: DataObjectIcon,
  delete: DeleteIcon,
  description: DescriptionIcon,
  download: DownloadIcon,
  error: ErrorIcon,
  expand_less: ExpandLessIcon,
  expand_more: ExpandMoreIcon,
  folder_open: FolderOpenIcon,
  forum: ForumIcon,
  help: HelpIcon,
  home: HomeIcon,
  inbox: InboxIcon,
  inventory_2: Inventory2Icon,
  list: ListIcon,
  lock: LockIcon,
  lock_open: LockOpenIcon,
  more_vert: MoreVertIcon,
  play_arrow: PlayArrowIcon,
  play_circle: PlayCircleIcon,
  radio_button_unchecked: RadioButtonUncheckedIcon,
  restart_alt: RestartAltIcon,
  rocket_launch: RocketLaunchIcon,
  save: SaveIcon,
  schedule: ScheduleIcon,
  settings: SettingsIcon,
  skip_next: SkipNextIcon,
  smart_toy: SmartToyIcon,
  stream: StreamIcon,
  subtitles: SubtitlesIcon,
  sync: SyncIcon,
  text_fields: TextFieldsIcon,
  timer: TimerIcon,
  toggle_on: ToggleOnIcon,
}

interface MaterialIconProps extends SvgIconProps {
  name: MaterialIconName | string
}

export const MaterialIcon: FC<MaterialIconProps> = ({ name, ...props }) => {
  const Icon = ICON_MAP[name as MaterialIconName]
  if (!Icon) {
    console.warn(`Unknown material icon: ${name}`)
    return null
  }
  return <Icon {...props} />
}
