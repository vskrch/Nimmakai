/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: '#09090b',
          sidebar: '#050505',
        },
        card: {
          DEFAULT: 'rgba(255, 255, 255, 0.03)',
          hover: 'rgba(255, 255, 255, 0.05)',
        },
        border: {
          DEFAULT: 'rgba(255, 255, 255, 0.08)',
          focus: 'rgba(139, 92, 246, 0.5)',
        },
        accent: '#8b5cf6',
        dim: '#a1a1aa',
        muted: '#52525b',
      },
    },
  },
  plugins: [],
}
