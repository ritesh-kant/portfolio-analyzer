import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#f4f0e8',
        panel: '#fffaf2',
        ink: '#17212a',
        accent: '#0f766e',
        accentWarm: '#c75a1b',
      },
      fontFamily: {
        display: ['"Space Grotesk"', 'sans-serif'],
        body: ['"Manrope"', 'sans-serif'],
      },
      boxShadow: {
        card: '0 10px 30px rgba(23, 33, 42, 0.08)',
      },
    },
  },
  plugins: [],
};

export default config;
