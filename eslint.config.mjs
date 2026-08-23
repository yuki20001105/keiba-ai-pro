import nextVitals from 'eslint-config-next/core-web-vitals'
import nextTypescript from 'eslint-config-next/typescript'

export default [
  {
    ignores: [
      '**/.next/**',
      '**/.venv/**',
      '**/node_modules/**',
      '**/e2e-report/**',
      '**/test-results/**',
      '**/coverage/**',
    ],
  },
  ...nextVitals,
  ...nextTypescript,
  {
    files: ['**/*.{js,jsx,ts,tsx}'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      'react-hooks/immutability': 'off',
      'react-hooks/purity': 'off',
      'react-hooks/preserve-manual-memoization': 'off',
      'react-hooks/set-state-in-effect': 'off',
    },
  },
]
