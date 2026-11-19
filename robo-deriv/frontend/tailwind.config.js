/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      },
      colors: {
        surface: {
          950: '#050811',
          900: '#0a0f1d',
          850: '#0e1526',
          800: '#131c31',
          700: '#1b2742',
        },
      },
      boxShadow: {
        glow: '0 0 24px -4px rgba(16, 185, 129, 0.25)',
        'glow-cyan': '0 0 24px -4px rgba(6, 182, 212, 0.25)',
        'glow-rose': '0 0 24px -4px rgba(244, 63, 94, 0.3)',
      },
    },
  },
  plugins: [],
};
