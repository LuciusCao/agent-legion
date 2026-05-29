import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-explicit-any': 'warn',
    },
  },
  {
    files: ['src/**/*.test.ts', 'src/**/*.test.tsx', 'src/types/**/*.d.ts'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
)
