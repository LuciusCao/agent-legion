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

export type QuestionNormalized = {
  stem?: string
  options?: Record<string, unknown>[]
  answer?: unknown
  analysis?: unknown
  answer_blanks?: AnswerBlank[]
  analysis_steps?: AnalysisStep[][]
}
