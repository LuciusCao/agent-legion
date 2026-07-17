import type { components } from './generated/api'

// The types below model job artifact JSON (questions_parsed / comprehension
// info files), which is not covered by any OpenAPI response schema, so they
// stay hand-written. QuestionNormalized is covered by the generated schema
// and derived from it at the bottom of this file.

export type SocraticOption = {
  label?: string
  text?: string
  is_correct?: boolean
}

export type AnswerBlank = {
  alternatives: string[]
  is_latex: boolean
}

export type AnalysisStep = {
  content: string
  title?: string
  step: number
}

export type KeyInfoPosition = {
  start: number
  end: number
}

export type KeyInfoContent = {
  text?: string
  derived_text?: string
  derivation?: string
  position: KeyInfoPosition
}

export type KeyInfoItem = {
  key_info_id: string
  type: 'given' | 'hidden'
  content: KeyInfoContent
  question?: {
    text: string
    options: SocraticOption[]
  }
  question_comprehension_ability: string
}

export type PossibleErrorItem = {
  error_id: string
  error_type: 'question_comprehension'
  position: number
  error_answer: string[]
  error_description: string
  related_key_info_ids: string[]
}

export type ComprehensionInfo = {
  question_id: string
  fingerprint?: string | null
  fingerprint_source?: string
  fingerprint_missing?: boolean
  comprehension_data: {
    comprehension_difficulty?: number
    key_info_list: KeyInfoItem[]
    possible_error_list: PossibleErrorItem[]
  }
}

export type QuestionNormalized = components['schemas']['QuestionNormalized']

/** questions.json artifact view of QuestionNormalized, with precise blank/step items. */
export type QuestionArtifactNormalized = Omit<
  QuestionNormalized,
  'answer_blanks' | 'analysis_steps'
> & {
  answer_blanks?: AnswerBlank[] | null
  analysis_steps?: AnalysisStep[][] | null
}
