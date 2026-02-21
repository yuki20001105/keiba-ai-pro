import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // ブラックテーマ
        surface: {
          base:   '#0a0a0a', // 最深背景
          card:   '#111111', // カード背景
          raised: '#1a1a1a', // ホバー要素
        },
        border: {
          DEFAULT: '#1e1e1e',
          subtle:  '#2a2a2a',
        },
        text: {
          primary:   '#ffffff',
          secondary: '#888888',
          muted:     '#555555',
        },
        accent: {
          DEFAULT: '#ffffff',
          dim:     '#cccccc',
        },
        positive: '#4ade80', // 利益+
        negative: '#f87171', // 利益-
      },
    },
  },
  plugins: [],
};
export default config;
