import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // In a pnpm monorepo, Next.js output file tracing records absolute paths
  // into the pnpm virtual store on the build machine. Setting this to the
  // workspace root makes traces relative to it so all required files are
  // bundled into the Vercel prebuilt output and available at deploy time.
  outputFileTracingRoot: path.join(__dirname, '../../'),
};

export default nextConfig;
