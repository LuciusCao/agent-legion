import type { ComprehensionInfo } from '../types'

interface ComprehensionArtifact {
  question_id?: unknown
  fingerprint?: unknown
  fingerprint_source?: unknown
  fingerprint_missing?: unknown
  comprehension_data?: unknown
}

interface KeyInfoArtifact {
  question_id?: unknown
  key_info_list?: unknown
}

interface PossibleErrorsArtifact {
  question_id?: unknown
  possible_error_list?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function extractComprehensionInfo(
  data: ComprehensionArtifact
): ComprehensionInfo | null {
  if (!isRecord(data)) return null
  const comprehensionData = data.comprehension_data
  if (!isRecord(comprehensionData)) return null
  const keyInfoList = comprehensionData.key_info_list
  if (!Array.isArray(keyInfoList) || keyInfoList.length === 0) return null
  return data as ComprehensionInfo
}

function extractListFromArtifact(
  data: unknown
): { keyInfoList: unknown[]; possibleErrorList: unknown[] } | null {
  if (!isRecord(data)) return null
  const keyInfoList = data.key_info_list
  const possibleErrorList = data.possible_error_list
  const hasKeyInfo = Array.isArray(keyInfoList) && keyInfoList.length > 0
  const hasPossibleErrors =
    Array.isArray(possibleErrorList) && possibleErrorList.length > 0
  if (!hasKeyInfo && !hasPossibleErrors) return null
  return {
    keyInfoList: hasKeyInfo ? keyInfoList : [],
    possibleErrorList: hasPossibleErrors ? possibleErrorList : [],
  }
}

export function buildComprehensionInfo(
  keyInfoData: KeyInfoArtifact | null,
  possibleErrorsData: PossibleErrorsArtifact | null
): ComprehensionInfo | null {
  const keyInfoExtracted = keyInfoData
    ? extractListFromArtifact(keyInfoData)
    : null
  const possibleErrorsExtracted = possibleErrorsData
    ? extractListFromArtifact(possibleErrorsData)
    : null

  const keyInfoList = keyInfoExtracted?.keyInfoList
  const possibleErrorList = possibleErrorsExtracted?.possibleErrorList

  if (!keyInfoList && !possibleErrorList) return null

  return {
    question_id: String(
      keyInfoData?.question_id ?? possibleErrorsData?.question_id ?? ''
    ),
    fingerprint: null,
    fingerprint_source: 'missing',
    fingerprint_missing: true,
    comprehension_data: {
      key_info_list: keyInfoList ?? [],
      possible_error_list: possibleErrorList ?? [],
    },
  } as ComprehensionInfo
}

export type { KeyInfoArtifact, PossibleErrorsArtifact }
