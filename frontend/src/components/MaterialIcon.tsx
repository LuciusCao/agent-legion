import { FC } from 'react'
import { SvgIconProps } from '@mui/material/SvgIcon'
import { ICON_MAP, MaterialIconName } from './materialIconRegistry'

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

export type { MaterialIconName }
